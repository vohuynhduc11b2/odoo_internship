{
    'name': 'UC Website Sale Tier Price Fix',
    'version': '18.0.1.1.2',
    'summary': 'Fix website tier price table and active tier refresh',
    'description': '''
Fix bảng giá bậc thang trên trang sản phẩm:
- Loại bỏ các dòng bị chồng mốc / bị rule khác đè
- Hiển thị đúng khoảng giá hiệu lực cuối cùng
- Refresh đúng đơn giá, tổng cộng và dòng đang áp dụng khi đổi số lượng
''',
    'category': 'Website/eCommerce',
    'author': 'OpenAI',
    'license': 'LGPL-3',
    'depends': ['website_sale_custom'],
    'data': [],
    'assets': {
        'web.assets_frontend': [
            'uc_website_sale_tier_price_fix/static/src/js/tier_price_fix.js',
            'uc_website_sale_tier_price_fix/static/src/scss/tier_price_fix.scss',
        ],
    },
    'installable': True,
    'application': False,
}
