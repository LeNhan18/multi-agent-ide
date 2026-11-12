"""Small stateless Streamable HTTP MCP bridge for owner-configured office tools.

Provider credentials stay in the backend process. Word works on a dedicated
workspace and creates drafts only. This is not a Word desktop automation service.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import uuid
import zipfile
from pathlib import Path

import httpx
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from lxml.etree import LxmlError

router = APIRouter(prefix="/mcp", tags=["MCP Office"])
PROTOCOL = "2025-06-18"


def tool(name: str, description: str, fields: dict[str, str], write: bool = False, optional: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": {k: {"type": "string", "description": v, "maxLength": 12000} for k, v in fields.items()},
                            "required": [k for k in fields if k not in optional], "additionalProperties": False},
            "annotations": {"readOnlyHint": not write, "destructiveHint": False}}


TOOLS = {
    "word": [tool("word_list", "Liệt kê tối đa 100 tài liệu DOCX trong workspace.", {}),
             tool("word_read", "Đọc đoạn văn và bảng trong một DOCX (không gồm ảnh, chú thích, header/footer).", {"path": "Đường dẫn tương đối trong workspace"}),
             tool("word_create_draft", "Tạo DOCX mới trong .agent-drafts; không ghi đè tài liệu gốc.", {"title": "Tiêu đề", "text": "Nội dung văn bản"}, True)],
    "notion": [tool("notion_search", "Tìm tối đa 20 trang được chia sẻ cho integration.", {"query": "Từ khóa tiêu đề"}),
               tool("notion_read", "Đọc tối đa 100 block con trực tiếp của một trang; không đệ quy.", {"page_id": "ID trang Notion"}),
               tool("notion_append", "Thêm đoạn văn vào trang Notion đã chọn.", {"page_id": "ID trang Notion", "text": "Nội dung đoạn văn, tối đa 2000 ký tự"}, True)],
    "outlook": [tool("outlook_list", "Đọc thông tin cơ bản của 20 email mới nhất; không đọc toàn bộ nội dung.", {}),
                tool("outlook_create_draft", "Tạo email nháp; không gửi email.", {"subject": "Tiêu đề", "text": "Nội dung", "to": "Một địa chỉ email (có thể để trống)"}, True, ("to",))],
}


def workspace() -> Path:
    configured = os.environ.get("MCP_WORD_WORKSPACE")
    if not configured:
        raise ValueError("Word workspace chưa được cấu hình")
    root = Path(configured).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Word workspace không hợp lệ")
    return root


def word_call(name: str, args: dict) -> dict:
    root = workspace()
    if name == "word_list":
        # Bound traversal and returned data. Do not follow symlink directories.
        found = []
        visited = 0
        for directory, subdirs, files in os.walk(root, followlinks=False):
            subdirs[:] = [d for d in subdirs if not (Path(directory) / d).is_symlink()][:50]
            visited += 1
            if visited > 200:
                break
            for filename in files:
                p = Path(directory) / filename
                if p.suffix.lower() == ".docx" and not p.is_symlink():
                    found.append(str(p.relative_to(root)))
                    if len(found) >= 100:
                        return {"files": found, "truncated": True}
        return {"files": found, "truncated": visited > 200}
    if name == "word_read":
        path = (root / args["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or path.suffix.lower() != ".docx" or not path.is_file():
            raise ValueError("Đường dẫn phải là DOCX trong workspace")
        with path.open("rb") as source:
            data = source.read(10_000_001)
        if len(data) > 10_000_000:
            raise ValueError("DOCX quá lớn (giới hạn 10 MB)")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 20_000_000:
                    raise ValueError("DOCX giải nén vượt giới hạn")
            doc = Document(io.BytesIO(data))
        except (KeyError, LxmlError, PackageNotFoundError, zipfile.BadZipFile) as error:
            raise ValueError("DOCX không hợp lệ") from error
        text = "\n".join([p.text for p in doc.paragraphs] + [" | ".join(c.text for c in row.cells) for t in doc.tables for row in t.rows])
        return {"path": str(path.relative_to(root)), "text": text[:100000], "truncated": len(text) > 100000}
    draft_dir = root / ".agent-drafts"
    if draft_dir.is_symlink():
        raise ValueError("Thư mục draft không được là symlink")
    draft_dir.mkdir(exist_ok=True)
    if not draft_dir.resolve().is_relative_to(root):
        raise ValueError("Draft ngoài workspace")
    doc = Document()
    doc.add_heading(args["title"], 0)
    for line in args["text"].splitlines():
        doc.add_paragraph(line)
    path = draft_dir / (uuid.uuid4().hex + ".docx")
    with path.open("xb") as output:
        doc.save(output)
    return {"path": str(path.relative_to(root)), "status": "draft_created"}


async def provider_call(service: str, name: str, args: dict) -> dict:
    if service == "word":
        return await asyncio.to_thread(word_call, name, args)
    token = os.environ.get("NOTION_API_KEY" if service == "notion" else "OUTLOOK_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError("Chưa cấu hình tài khoản nhà cung cấp")
    headers = {"Authorization": "Bearer " + token}
    payload = None
    method = "GET"
    if service == "notion":
        headers["Notion-Version"] = "2025-09-03"
        base = "https://api.notion.com/v1"
        if name == "notion_search":
            method, url, payload = "POST", base + "/search", {"query": args["query"], "page_size": 20, "filter": {"property": "object", "value": "page"}}
        else:
            page_id = str(uuid.UUID(args["page_id"]))
            url = base + "/blocks/" + page_id + "/children?page_size=100"
            if name == "notion_append":
                if len(args["text"]) > 2000:
                    raise ValueError("Đoạn văn Notion tối đa 2000 ký tự")
                method = "PATCH"
                payload = {"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": args["text"]}}]}}]}
    else:
        url = "https://graph.microsoft.com/v1.0/me/messages"
        if name == "outlook_list":
            url += "?$top=20&$select=id,subject,from,receivedDateTime,isRead&$orderby=receivedDateTime%20desc"
        else:
            recipient = args.get("to", "").strip()
            if recipient and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", recipient):
                raise ValueError("Địa chỉ email không hợp lệ")
            method = "POST"
            payload = {"subject": args["subject"], "body": {"contentType": "Text", "content": args["text"]},
                       "toRecipients": [{"emailAddress": {"address": recipient}}] if recipient else []}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async with client.stream(method, url, headers=headers, json=payload) as response:
            if response.status_code >= 300:
                raise ValueError("Nhà cung cấp trả HTTP " + str(response.status_code) + "; kiểm tra quyền hoặc token")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 400000:
                    raise ValueError("Kết quả quá lớn; thu hẹp truy vấn")
            # Credential values must never be relayed, even if reflected upstream.
            return json.loads(bytes(data).decode().replace(token, "[REDACTED]"))


def reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


@router.post("/{service}")
async def mcp(service: str, request: Request) -> Response:
    if service not in TOOLS:
        raise HTTPException(404)
    expected = os.environ.get("MCP_BRIDGE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(503, "MCP bridge chưa được cấu hình")
    authorization = request.headers.get("authorization", "")
    if not secrets.compare_digest(authorization.encode("utf-8"), ("Bearer " + expected).encode("utf-8")):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
    origin = request.headers.get("origin")
    allowed = os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",")
    if origin and origin not in allowed:
        raise HTTPException(403)
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 16000:
            raise HTTPException(413)
    try:
        body = json.loads(data, parse_constant=reject_json_constant)
    except (ValueError, RecursionError):
        raise HTTPException(400, "JSON không hợp lệ")
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        raise HTTPException(400)
    request_id, method = body.get("id"), body.get("method")
    def result(value: dict) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": value}, headers={"Cache-Control": "no-store"})
    def error(code: int, message: str) -> JSONResponse:
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, headers={"Cache-Control": "no-store"})
    if method == "notifications/initialized" and request_id is None:
        return Response(status_code=202)
    if type(request_id) not in (int, str):
        request_id = None
        return error(-32600, "Request id must be a string or integer")
    if isinstance(request_id, str):
        try:
            request_id.encode("utf-8")
        except UnicodeEncodeError:
            request_id = None
            return error(-32600, "Request id is not valid Unicode")
    if method == "initialize":
        return result({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "ai-office-" + service, "version": "1.0.0"}})
    if request.headers.get("mcp-protocol-version") not in (PROTOCOL, "2025-11-25"):
        return error(-32600, "Unsupported protocol version")
    if method == "tools/list":
        return result({"tools": TOOLS[service]})
    if method != "tools/call":
        return error(-32601, "Method not supported")
    params = body.get("params")
    if not isinstance(params, dict):
        return error(-32602, "Invalid parameters")
    spec = next((t for t in TOOLS[service] if t["name"] == params.get("name")), None)
    args = params.get("arguments", {})
    if not spec or not isinstance(args, dict):
        return error(-32602, "Tool or arguments invalid")
    schema = spec["inputSchema"]
    if set(args) - set(schema["properties"]) or any(k not in args for k in schema["required"]) or any(not isinstance(v, str) or len(v) > 12000 for v in args.values()):
        return error(-32602, "Arguments do not match schema")
    if not spec["annotations"]["readOnlyHint"] and (not isinstance(params.get("_meta"), dict) or params["_meta"].get("ai-office/confirmed") is not True):
        return error(-32602, "Explicit confirmation required")
    try:
        output = await provider_call(service, spec["name"], args)
        return result({"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}], "isError": False})
    except (ValueError, OSError, httpx.HTTPError, zipfile.BadZipFile):
        # Do not expose provider bodies, filesystem paths or credential values.
        return result({"content": [{"type": "text", "text": "Công cụ thất bại. Kiểm tra token/quyền, tham số, workspace và giới hạn dữ liệu; không tự thử lại thao tác ghi."}], "isError": True})
