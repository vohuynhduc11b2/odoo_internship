{
    'name': 'OMS Payment Incident Tracker',
    'version': '18.0.1.0.1',
    'summary': 'Theo dõi lỗi và cảnh báo cho đặt hàng/thanh toán',
    'description': '''
Theo dõi lỗi và cảnh báo phát sinh trong quá trình đặt hàng/thanh toán.
- Nhật ký sự cố gắn với giao dịch thanh toán và đơn bán
- Ghi nhận nguyên nhân gốc và cách phòng tránh
- Tự động cảnh báo giao dịch pending quá lâu
- Tự động ghi nhận lỗi khi giao dịch bị error/cancel
- Bộ lọc Q1/2026 để phục vụ tổng kết và hạn chế lỗi lặp lại
    ''',
    'category': 'Accounting/Payment Providers',
    'author': 'OpenAI',
    'license': 'LGPL-3',
    'depends': ['payment', 'sale', 'mail'],
    'data': [
        'security/payment_incident_security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/payment_incident_data.xml',
        'data/ir_cron.xml',
        'wizard/payment_incident_wizard_views.xml',
        'views/payment_incident_views.xml',
        'views/payment_transaction_views.xml',
        'views/sale_order_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
}
