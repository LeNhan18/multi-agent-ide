(() => {
'use strict';
// Slots follow the shared workflow: produce, review, deliver, audit, intake, specialist.
const slots=['Thực hiện','Kiểm duyệt','Bàn giao','Kiểm tra nhật ký','Tiếp nhận','Phân tích chuyên môn'];
const profiles={
 general:['Vận hành chung','Báo cáo và kế hoạch hành động',['Chuyên viên vận hành','Kiểm soát chất lượng','Điều phối bàn giao','Kiểm toán quy trình','Tiếp nhận yêu cầu','Phân tích nghiệp vụ']],
 engineering:['Kỹ thuật phần mềm','Bản vá đã được duyệt',['Lập trình viên','Review mã','Tích hợp bản vá','Auditor','Quản lý yêu cầu','Phân tích kỹ thuật']],
 research:['Nghiên cứu & giáo dục','Báo cáo tổng quan có trích dẫn',['Biên soạn nghiên cứu','Phản biện phương pháp','Biên tập báo cáo','Rà soát nguồn','Điều phối đề tài','Tìm tài liệu']],
 finance:['Tài chính & kế toán','Báo cáo đối soát chi phí',['Lập báo cáo','Kiểm soát tài chính','Tổng hợp hồ sơ','Kiểm toán nội bộ','Tiếp nhận chứng từ','Phân tích giao dịch']],
 legal:['Pháp lý & tuân thủ','Bản ghi nhớ rà soát điều khoản',['Soạn bản ghi nhớ','Rà soát pháp lý','Quản lý hồ sơ','Kiểm tra tuân thủ','Tiếp nhận vụ việc','Tra cứu điều khoản']],
 healthcare:['Vận hành y tế','Báo cáo luồng tiếp nhận từ dữ liệu giả lập',['Lập báo cáo vận hành','Kiểm tra chất lượng dữ liệu','Điều phối báo cáo','Rà soát bảo mật','Tiếp nhận yêu cầu','Phân tích quy trình']],
 marketing:['Marketing & truyền thông','Bộ nội dung chiến dịch chờ bàn giao',['Sản xuất nội dung','Duyệt thương hiệu','Điều phối chiến dịch','Kiểm tra nguồn','Tiếp nhận brief','Nghiên cứu khán giả']],
 logistics:['Sản xuất & logistics','Kế hoạch điều phối đơn hàng',['Lập kế hoạch','Kiểm tra chất lượng','Điều phối kho vận','Truy vết quy trình','Tiếp nhận đơn hàng','Phân tích năng lực']],
 service:['Bán hàng & chăm sóc khách hàng','Phương án xử lý yêu cầu khách hàng',['Soạn phương án','Duyệt chính sách','Điều phối phản hồi','Kiểm tra hồ sơ','Tiếp nhận yêu cầu','Phân tích lịch sử']],
 construction:['Xây dựng & mua sắm','Hồ sơ đối chiếu BOQ và báo giá',['Lập hồ sơ','Rà soát kỹ thuật','Bàn giao đề xuất','Kiểm tra truy vết','Tiếp nhận BOQ','Đối chiếu vật tư']]
};
window.officeSquads={slots,profiles};
})();
