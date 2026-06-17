#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug script để kiểm tra mapping khung giá.
"""
import sys
sys.path.insert(0, '/odoo')

# Search for the product
product = env['product.product'].search([('name', 'ilike', 'Keypad')], limit=1)
if product:
    print(f"✓ Product: {product.name} (ID: {product.id})")
    print(f"  Default Code: {product.default_code}")
    
    # Search OMS pricelist lines
    oms_lines = env['oms.price.list.line'].search([("item_id", "=", product.id)])
    print(f"\n✓ Found {len(oms_lines)} OMS lines")
    
    if oms_lines:
        for line in oms_lines[:5]:
            print(f"  - OMS Pricelist: {line.pricelist_id.name}")
            print(f"    Item: {line.item_id.name} (ID: {line.item_id.id})")
            print(f"    Frame: {line.price_frame_id.price_list_name if line.price_frame_id else 'NO FRAME'}")
            print(f"    Frame ID: {line.price_frame_id.id if line.price_frame_id else 'None'}")
            print()
    else:
        print("  No OMS lines found!")
else:
    print("❌ Product not found")

# Search all products with code containing "11022"
print("\n=== All products with code containing '11022' ===")
products = env['product.product'].search([('default_code', 'ilike', '11022')])
print(f"Found {len(products)} products:")
for p in products[:5]:
    print(f"  - {p.name} (Code: {p.default_code}, ID: {p.id})")
    # Check for OMS lines
    oms_count = env['oms.price.list.line'].search_count([("item_id", "=", p.id)])
    print(f"    OMS lines: {oms_count}")
