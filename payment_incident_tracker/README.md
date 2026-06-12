# OMS Payment Incident Tracker

Addon Odoo 18 dùng để theo dõi lỗi/cảnh báo ở giai đoạn đặt hàng - thanh toán, tập trung cho các tình huống phát sinh ở `payment.transaction`.

## Chức năng chính

- Bảng theo dõi sự cố/cảnh báo thanh toán.
- Ghi nhận đầy đủ: hiện tượng, nguyên nhân gốc, cách phòng tránh, hướng xử lý.
- Liên kết trực tiếp với:
  - Giao dịch thanh toán (`payment.transaction`)
  - Đơn bán hàng (`sale.order`)
- Tự động tạo **cảnh báo** khi giao dịch `draft/pending` quá lâu.
- Tự động tạo **lỗi** khi giao dịch chuyển sang `error/cancel`.
- Bộ lọc sẵn **Q1/2026** để phục vụ tổng hợp lỗi lặp lại.
- Wizard ghi nhận nhanh sự cố từ form giao dịch thanh toán hoặc đơn bán hàng.

## Cấu hình nhanh

Sau khi cài addon, có thể chỉnh 2 tham số hệ thống nếu cần:

- `payment_incident_tracker.auto_warning` = `True/False`
- `payment_incident_tracker.pending_minutes` = `30`

## Menu

- **Theo dõi thanh toán / Sự cố/Cảnh báo**
- **Theo dõi thanh toán / Cấu hình / Danh mục sự cố**

## Gợi ý vận hành

Dùng danh mục để chuẩn hóa các lỗi lặp lại như:

- Thanh toán treo quá lâu
- Callback/webhook không về
- Số tiền không khớp
- Thiếu chứng từ UNC/chuyển khoản
- Khách hủy giữa chừng

Khi tổng kết Q1/2026, chỉ cần lọc `Q1/2026` trong màn hình sự cố để xem thống kê và lập CAPA.
