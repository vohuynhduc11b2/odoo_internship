/** @odoo-module **/

const formatOmsJsonNotes = () => {
    document.querySelectorAll('.oms_note_pretty[data-oms-note]').forEach((note) => {
        const raw = note.dataset.omsNote || note.textContent || '';
        let payload;
        try {
            payload = JSON.parse(raw);
        } catch {
            note.textContent = raw;
            return;
        }

        const info = payload.info || payload;
        const labels = {
            ItemModel: 'Mã hàng',
            ItemNote: 'Mô tả',
            AlterItem: 'Mã thay thế',
            StockType: 'Loại tồn',
            MinStock: 'Tồn tối thiểu',
        };

        note.textContent = '';
        note.classList.add('is_json');

        Object.entries(labels).forEach(([key, label]) => {
            const value = info[key];
            const hasValue = Array.isArray(value)
                ? value.some((item) => item !== null && item !== undefined && item !== '')
                : value !== null && value !== undefined && value !== '';
            if (!hasValue) {
                return;
            }

            const item = document.createElement('span');
            item.className = 'oms_note_item';

            const labelEl = document.createElement('strong');
            labelEl.textContent = label;

            const valueEl = document.createElement('span');
            valueEl.textContent = Array.isArray(value) ? value.filter(Boolean).join(', ') : value;

            item.append(labelEl, valueEl);
            note.appendChild(item);
        });
    });
};

const initOmsProductDetail = () => {
    const page = document.querySelector('.oms_prime_product');
    if (!page || page.dataset.omsProductDetailReady === '1') {
        return;
    }
    page.dataset.omsProductDetailReady = '1';

    const snapshotElement = (selector) => {
        const element = document.querySelector(selector);
        return element ? { selector, html: element.outerHTML } : null;
    };
    const createElementFromHtml = (html) => {
        const template = document.createElement('template');
        template.innerHTML = html.trim();
        return template.content.firstElementChild;
    };
    const preservedBlocks = [
        snapshotElement('.oms_pd_meta'),
        snapshotElement('.oms_pd_badges'),
        snapshotElement('.oms_pd_short_desc'),
        snapshotElement('.oms_pd_hero_overlay'),
        snapshotElement('.oms_pd_quick_facts'),
        snapshotElement('.oms_purchase_card'),
        snapshotElement('.oms_pd_quick_links'),
        snapshotElement('.oms_pd_trust_grid'),
        snapshotElement('.oms_pd_sticky_mobile'),
        snapshotElement('.oms_pd_dashboard'),
    ].filter(Boolean);

    let isRestoringOmsProductDetail = false;
    const restoreBlock = (block) => {
        if (document.querySelector(block.selector)) {
            return;
        }
        const element = createElementFromHtml(block.html);
        const details = document.querySelector('#product_details');
        const title = details?.querySelector('h1');
        const gallery = document.querySelector('.oms_pd_hero_gallery, div[t-att-data-image-amount]');
        const optionBlock = document.querySelector('#product_option_block');
        const productDetail = document.querySelector('#product_detail');

        if (['.oms_pd_meta', '.oms_pd_badges', '.oms_pd_short_desc'].includes(block.selector) && title) {
            const anchors = ['.oms_pd_short_desc', '.oms_pd_badges', '.oms_pd_meta']
                .map((selector) => details.querySelector(selector))
                .filter(Boolean);
            (anchors[0] || title).after(element);
            return;
        }
        if (block.selector === '.oms_pd_hero_overlay' && gallery) {
            gallery.appendChild(element);
            return;
        }
        if (block.selector === '.oms_pd_quick_facts' && gallery) {
            gallery.after(element);
            return;
        }
        if (block.selector === '.oms_purchase_card' && details) {
            const price = details.querySelector('.product_price, .o_wsale_product_price');
            (price || details.lastElementChild || details).after(element);
            return;
        }
        if (['.oms_pd_quick_links', '.oms_pd_trust_grid', '.oms_pd_sticky_mobile'].includes(block.selector) && optionBlock) {
            const anchors = ['.oms_pd_sticky_mobile', '.oms_pd_trust_grid', '.oms_pd_quick_links']
                .map((selector) => document.querySelector(selector))
                .filter(Boolean);
            (anchors[0] || optionBlock).after(element);
            return;
        }
        if (block.selector === '.oms_pd_dashboard' && productDetail) {
            productDetail.after(element);
        }
    };
    const restoreOmsProductDetail = () => {
        if (isRestoringOmsProductDetail) {
            return;
        }
        isRestoringOmsProductDetail = true;
        preservedBlocks.forEach(restoreBlock);
        isRestoringOmsProductDetail = false;
    };

    restoreOmsProductDetail();
    if (preservedBlocks.length) {
        const scheduleRestoreOmsProductDetail = () => {
            requestAnimationFrame(restoreOmsProductDetail);
            setTimeout(restoreOmsProductDetail, 0);
            setTimeout(restoreOmsProductDetail, 100);
            setTimeout(restoreOmsProductDetail, 500);
        };
        const observer = new MutationObserver(scheduleRestoreOmsProductDetail);
        observer.observe(document.body, { childList: true, subtree: true });
        ['change', 'click', 'input', 'mouseup', 'keyup'].forEach((eventName) => {
            document.addEventListener(eventName, scheduleRestoreOmsProductDetail, true);
        });
        window.addEventListener('resize', scheduleRestoreOmsProductDetail);
        window.addEventListener('popstate', scheduleRestoreOmsProductDetail);
        setInterval(restoreOmsProductDetail, 300);
        [50, 100, 250, 500, 1000, 2000, 4000].forEach((delay) => {
            setTimeout(restoreOmsProductDetail, delay);
        });
    }

    const qtyInput = document.querySelector('.js_main_product input[name="add_qty"], .css_quantity input');
    const subtotalLabel = page.querySelector('.oms_pd_qty_value');
    const subtotalPrice = page.querySelector('.oms_pc_subtotal_price');
    const priceDisplay = page.querySelector('#product_details > .product_price, #product_details > .o_wsale_product_price, .product_price, .o_wsale_product_price');
    const stickyPrice = page.querySelector('.oms_sticky_price');
    const stickyCartBtn = page.querySelector('.oms_sticky_cart_btn');
    const addToCartBtn = document.querySelector('#add_to_cart');
    const tierRows = [...page.querySelectorAll('.oms_price_tiers_table tbody tr[data-oms-tier-min]')];
    const hideEmptyBlocks = () => {
        page.querySelectorAll('#product_option_block, .js_product, .css_quantity').forEach((el) => {
            const hasInteractive = el.querySelector('input, button, select, a, textarea');
            const hasText = (el.textContent || '').trim().length > 0;
            if (!hasInteractive && !hasText) {
                el.style.display = 'none';
            }
        });
    };

    hideEmptyBlocks();

    const formatPrice = (el) => {
        if (!el) {
            return '';
        }
        const clone = el.cloneNode(true);
        clone.querySelectorAll('.o_wsale_discount, small, .text-decoration-line-through').forEach((n) => n.remove());
        return clone.textContent.trim();
    };

    const getUnitPrice = () => {
        if (!priceDisplay) {
            return 0;
        }
        const priceText = formatPrice(priceDisplay);
        const cleaned = priceText.replace(/[^\d.,]/g, '').replace(/\./g, '').replace(',', '.');
        return parseFloat(cleaned) || 0;
    };

    const formatCurrency = (amount) => {
        if (!amount) {
            return '';
        }
        return new Intl.NumberFormat('vi-VN').format(Math.round(amount)) + ' \u20ab';
    };

    const syncQtyDisplay = () => {
        const qty = parseInt(qtyInput?.value, 10) || 1;
        if (subtotalLabel) {
            subtotalLabel.textContent = qty.toString();
        }
        const unitPrice = getUnitPrice();
        if (subtotalPrice && unitPrice) {
            subtotalPrice.textContent = formatCurrency(unitPrice * qty);
        } else if (subtotalPrice && priceDisplay) {
            subtotalPrice.textContent = formatPrice(priceDisplay);
        }
        // Active tier row
        tierRows.forEach((row) => {
            const min = parseInt(row.dataset.omsTierMin, 10) || 0;
            const max = parseInt(row.dataset.omsTierMax, 10) || Infinity;
            row.classList.toggle('active', qty >= min && qty <= max);
        });
    };

    const syncStickyPrice = () => {
        if (stickyPrice && priceDisplay) {
            stickyPrice.textContent = formatPrice(priceDisplay);
        }
    };

    qtyInput?.addEventListener('input', syncQtyDisplay);
    qtyInput?.addEventListener('change', syncQtyDisplay);

    // Sync sticky CTA
    if (stickyCartBtn && addToCartBtn) {
        stickyCartBtn.addEventListener('click', () => {
            addToCartBtn.click();
        });
    }

    // Show sticky bar on scroll past CTA (mobile only)
    const stickyBar = page.querySelector('.oms_pd_sticky_mobile');
    const ctaWrapper = document.querySelector('#o_wsale_cta_wrapper');
    if (stickyBar && ctaWrapper) {
        const mq = window.matchMedia('(max-width: 575.98px)');
        const toggle = () => {
            if (!mq.matches) {
                stickyBar.style.display = 'none';
                return;
            }
        };
        const observer = new IntersectionObserver(([entry]) => {
            if (!mq.matches) {
                return;
            }
            stickyBar.style.display = entry.isIntersecting ? 'none' : 'flex';
        }, { threshold: 0 });
        observer.observe(ctaWrapper);
        mq.addEventListener('change', toggle);
        toggle();
    }

    // Initial sync
    syncQtyDisplay();
    syncStickyPrice();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initOmsProductDetail();
        formatOmsJsonNotes();
    });
} else {
    initOmsProductDetail();
    formatOmsJsonNotes();
}
