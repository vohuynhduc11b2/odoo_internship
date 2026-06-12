/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

const debounce = (fn, delay = 500) => {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
};

const normalize = (value) => (value || '').toString().toLowerCase().trim();

const updateCartQuantity = (cartQuantity) => {
    sessionStorage.setItem('website_sale_cart_quantity', cartQuantity);
    document.querySelectorAll('.my_cart_quantity').forEach((quantity) => {
        quantity.textContent = cartQuantity ? cartQuantity.toString() : '';
        quantity.classList.toggle('d-none', !cartQuantity);
        quantity.closest('li.o_wsale_my_cart')?.classList.remove('d-none');
    });
};

const playAddToCartAnimation = (form) => {
    const image = form.closest('.oms_product_card')?.querySelector('.oms_product_image_link img');
    const cart = document.querySelector('.o_wsale_my_cart a, .my_cart_quantity');
    if (!image || !cart) {
        return;
    }

    const imageRect = image.getBoundingClientRect();
    const cartRect = cart.getBoundingClientRect();
    const clone = image.cloneNode(true);
    clone.className = 'oms_fly_to_cart_item';
    clone.style.left = `${imageRect.left}px`;
    clone.style.top = `${imageRect.top}px`;
    clone.style.width = `${imageRect.width}px`;
    clone.style.height = `${imageRect.height}px`;
    clone.style.setProperty('--oms-fly-x', `${cartRect.left + (cartRect.width / 2) - imageRect.left - (imageRect.width / 2)}px`);
    clone.style.setProperty('--oms-fly-y', `${cartRect.top + (cartRect.height / 2) - imageRect.top - (imageRect.height / 2)}px`);
    document.body.appendChild(clone);
    clone.addEventListener('animationend', () => clone.remove(), { once: true });
};

const initOmsShop = () => {
    const root = document.querySelector('.oms_prime_shop');
    if (!root) {
        return;
    }

    const input = root.querySelector('.oms_search_form input[name="search"]');
    const cards = [...root.querySelectorAll('.oms_product_card')];
    const empty = root.querySelector('.oms_client_empty');
    const categoryChips = [...root.querySelectorAll('.oms_category_chip')];
    const categoryRadios = [...root.querySelectorAll('input[name="category"]')];
    const brandChecks = [...root.querySelectorAll('.oms_filter_box input[type="checkbox"]')];
    const filterBoxButtons = [...root.querySelectorAll('.oms_filter_box > button')];
    const viewButtons = [...root.querySelectorAll('.oms_view_toggle button')];
    const productGrid = root.querySelector('.oms_product_grid');
    const visibleCountElement = root.querySelector('.oms_visible_count');
    const totalCountElement = root.querySelector('.oms_total_count');
    const headerTotalCountElement = root.querySelector('.oms_header_total_count');
    const activeFilters = root.querySelector('.oms_active_filters');
    const filterTags = root.querySelector('.oms_filter_tags');
    const clearFiltersButton = root.querySelector('.oms_clear_filters');
    const emptyClearFiltersButton = root.querySelector('.oms_empty_clear_filters');
    const cartForms = [...root.querySelectorAll('.oms_add_to_cart_form, .oms_list_add_to_cart_form')];
    const excelFileInput = root.querySelector('.oms_excel_cart_file');
    const excelImportButton = root.querySelector('.oms_excel_import_btn');
    const excelImportResult = root.querySelector('.oms_excel_import_result');
    const excelFileText = root.querySelector('.oms_excel_file_text');
    const excelFileLabel = root.querySelector('.oms_excel_file_label');
    const quickCartTextarea = root.querySelector('.oms_quick_cart_textarea');
    const quickCartImportButton = root.querySelector('.oms_quick_cart_import_btn');
    const initialVisibleLimit = 48;
    const loadMoreStep = 12;
    let visibleLimit = initialVisibleLimit;

    if (!cards.length) {
        return;
    }

    let activeCategory = '';

    const getActiveBrands = () => brandChecks
        .filter((checkbox) => checkbox.checked)
        .map((checkbox) => normalize(checkbox.dataset.filterValue));

    const getFilterLabel = (inputElement) => inputElement?.closest('label')?.textContent?.trim() || inputElement?.dataset.filterValue || '';

    const resetCategoryFilter = () => {
        activeCategory = '';
        categoryChips.forEach((chip) => chip.classList.toggle('active', !normalize(chip.dataset.filterValue)));
        categoryRadios.forEach((radio) => {
            radio.checked = !normalize(radio.dataset.filterValue);
        });
    };

    const updateActiveFilters = () => {
        if (!activeFilters || !filterTags) {
            return;
        }

        filterTags.innerHTML = '';
        const selectedFilters = [];
        const activeCategoryRadio = categoryRadios.find((radio) => radio.checked && normalize(radio.dataset.filterValue));

        if (activeCategoryRadio) {
            selectedFilters.push({
                type: 'category',
                label: getFilterLabel(activeCategoryRadio),
            });
        }

        brandChecks
            .filter((checkbox) => checkbox.checked)
            .forEach((checkbox) => selectedFilters.push({
                type: 'brand',
                value: checkbox.value,
                label: getFilterLabel(checkbox),
            }));

        selectedFilters.forEach((filter) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'oms_filter_tag';
            button.dataset.filterType = filter.type;
            if (filter.value) {
                button.dataset.filterValue = filter.value;
            }
            const label = document.createElement('span');
            const icon = document.createElement('i');
            label.textContent = filter.label;
            icon.className = 'fa fa-times';
            icon.setAttribute('aria-hidden', 'true');
            button.append(label, icon);
            filterTags.appendChild(button);
        });

        activeFilters.hidden = selectedFilters.length === 0;
    };

    const applyFilter = () => {
        const term = normalize(input?.value);
        const brands = getActiveBrands();
        const matchingCards = cards.filter((card) => {
            const text = normalize(`${card.dataset.searchText || ''} ${card.dataset.category || ''} ${card.textContent || ''}`);
            const matchesTerm = !term || text.includes(term);
            const matchesCategory = !activeCategory || text.includes(activeCategory);
            const matchesBrand = !brands.length || brands.some((brand) => text.includes(brand));
            return matchesTerm && matchesCategory && matchesBrand;
        });

        cards.forEach((card) => {
            card.hidden = true;
        });
        matchingCards.slice(0, visibleLimit).forEach((card) => {
            card.hidden = false;
        });

        const shownCount = Math.min(visibleLimit, matchingCards.length);
        if (visibleCountElement) {
            visibleCountElement.textContent = shownCount.toString();
        }
        if (totalCountElement) {
            totalCountElement.textContent = matchingCards.length.toString();
        }
        if (headerTotalCountElement) {
            headerTotalCountElement.textContent = matchingCards.length.toString();
        }
        if (empty) {
            empty.hidden = matchingCards.length !== 0;
        }
        updateActiveFilters();
    };

    const debouncedApplyFilter = debounce(applyFilter, 500);
    
    // Auto-submit form to server for global search
    // Debounce 500ms to avoid too many requests
    let searchTimeout;
    input?.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            // Submit form to server for global search across all products
            // Not just 48 products on current page
            const form = input.closest('form');
            if (form && input.value.trim()) {
                form.submit();
            }
        }, 500);
    });
    
    // categoryChips.forEach - kept as is for category filtering (client-side)
    categoryChips.forEach((chip) => {
        chip.addEventListener('click', () => {
            categoryChips.forEach((item) => item.classList.remove('active'));
            chip.classList.add('active');
            activeCategory = normalize(chip.dataset.filterValue);
            categoryRadios.forEach((radio) => {
                radio.checked = normalize(radio.dataset.filterValue) === activeCategory;
            });
            if (!activeCategory && categoryRadios[0]) {
                categoryRadios[0].checked = true;
            }
            visibleLimit = initialVisibleLimit;
            applyFilter();
        });
    });

    categoryRadios.forEach((radio) => {
        radio.addEventListener('change', () => {
            activeCategory = normalize(radio.dataset.filterValue);
            categoryChips.forEach((chip) => {
                chip.classList.toggle('active', normalize(chip.dataset.filterValue) === activeCategory);
            });
            visibleLimit = initialVisibleLimit;
            applyFilter();
        });
    });

    const syncTagFilters = () => {
        const url = new URL(window.location.href);
        url.searchParams.delete('tags');
        brandChecks
            .filter((checkbox) => checkbox.name === 'tags' && checkbox.checked)
            .forEach((checkbox) => url.searchParams.append('tags', checkbox.value));
        window.location.assign(url.toString());
    };

    brandChecks.forEach((checkbox) => checkbox.addEventListener('change', () => {
        if (checkbox.name === 'tags') {
            syncTagFilters();
            return;
        }
        visibleLimit = initialVisibleLimit;
        applyFilter();
    }));

    const clearAllFilters = () => {
        brandChecks.forEach((checkbox) => {
            checkbox.checked = false;
        });
        if (input) {
            input.value = '';
        }
        resetCategoryFilter();
        visibleLimit = initialVisibleLimit;
        syncTagFilters();
    };

    clearFiltersButton?.addEventListener('click', clearAllFilters);
    emptyClearFiltersButton?.addEventListener('click', clearAllFilters);

    filterTags?.addEventListener('click', (event) => {
        const button = event.target.closest('.oms_filter_tag');
        if (!button) {
            return;
        }
        if (button.dataset.filterType === 'category') {
            resetCategoryFilter();
            visibleLimit = initialVisibleLimit;
            applyFilter();
            return;
        }
        const checkbox = brandChecks.find((item) => item.value === button.dataset.filterValue);
        if (checkbox) {
            checkbox.checked = false;
            visibleLimit = initialVisibleLimit;
            if (checkbox.name === 'tags') {
                syncTagFilters();
                return;
            }
            applyFilter();
        }
    });

    cartForms.forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const button = form.querySelector('.oms_add_to_cart_btn');
            if (button?.disabled) {
                return;
            }

            const formData = new FormData(form);
            const productId = Number(formData.get('product_id'));
            if (!productId) {
                form.submit();
                return;
            }

            button.disabled = true;
            form.classList.add('is-loading');
            try {
                playAddToCartAnimation(form);
                const data = await rpc('/shop/cart/update_json', {
                    product_id: productId,
                    product_template_id: Number(formData.get('product_template_id')) || undefined,
                    add_qty: Number(formData.get('add_qty')) || 1,
                    display: false,
                    force_create: true,
                });
                updateCartQuantity(Number(data.cart_quantity || 0));
                button.classList.add('is-added');
                setTimeout(() => button.classList.remove('is-added'), 1200);
            } catch (error) {
                form.submit();
            } finally {
                button.disabled = false;
                form.classList.remove('is-loading');
            }
        });
    });

    const renderExcelImportResult = (data) => {
        if (!excelImportResult) {
            return;
        }
        const failures = Array.isArray(data.failures) ? data.failures.slice(0, 5) : [];
        const failureItems = failures.map((item) => `
            <li>Dòng ${item.row || '-'} - ${item.code || 'N/A'}: ${item.reason || 'Lỗi import'}</li>
        `).join('');
        excelImportResult.innerHTML = `
            <strong>Kết quả import</strong>
            <div>Thêm thành công: ${data.success_count || 0} dòng (${data.added_qty || 0} sản phẩm)</div>
            <div>Thất bại: ${data.failed_count || 0} dòng</div>
            ${failureItems ? `<ul>${failureItems}</ul>` : ''}
        `;
        excelImportResult.hidden = false;
    };

    const fileToBase64 = (file) => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });

    const setExcelFile = (file) => {
        if (!excelFileInput || !file) {
            return;
        }
        const transfer = new DataTransfer();
        transfer.items.add(file);
        excelFileInput.files = transfer.files;
        if (excelFileText) {
            excelFileText.textContent = file.name;
        }
    };

    excelFileInput?.addEventListener('change', () => {
        const file = excelFileInput.files?.[0];
        if (excelFileText) {
            excelFileText.textContent = file ? file.name : 'Chưa chọn file';
        }
    });

    excelFileLabel?.addEventListener('dragover', (event) => {
        event.preventDefault();
        excelFileLabel.classList.add('is-dragover');
    });
    excelFileLabel?.addEventListener('dragleave', () => {
        excelFileLabel.classList.remove('is-dragover');
    });
    excelFileLabel?.addEventListener('drop', (event) => {
        event.preventDefault();
        excelFileLabel.classList.remove('is-dragover');
        const file = event.dataTransfer?.files?.[0];
        if (file) {
            setExcelFile(file);
        }
    });

    const toggleImportButton = (button, isLoading) => {
        if (!button) {
            return;
        }
        button.disabled = isLoading;
        button.classList.toggle('is-loading', isLoading);
    };

    excelImportButton?.addEventListener('click', async () => {
        const file = excelFileInput?.files?.[0];
        if (!file) {
            window.alert('Vui lòng chọn file Excel trước khi import.');
            excelFileInput?.focus();
            return;
        }
        toggleImportButton(excelImportButton, true);
        try {
            const data = await rpc('/shop/import_cart_excel', {
                file: await fileToBase64(file),
                filename: file.name,
            });
            renderExcelImportResult(data || {});
            updateCartQuantity(Number(data?.cart_quantity || 0));
        } catch (error) {
            renderExcelImportResult({
                success_count: 0,
                failed_count: 1,
                added_qty: 0,
                failures: [{ row: 0, code: file.name, reason: 'Không import được file' }],
            });
        } finally {
            toggleImportButton(excelImportButton, false);
        }
    });

    quickCartImportButton?.addEventListener('click', async () => {
        const text = quickCartTextarea?.value?.trim() || '';
        if (!text) {
            window.alert('Vui lòng nhập danh sách mã SP và số lượng trước.');
            quickCartTextarea?.focus();
            return;
        }
        toggleImportButton(quickCartImportButton, true);
        try {
            const data = await rpc('/shop/import_cart_text', { text });
            renderExcelImportResult(data || {});
            updateCartQuantity(Number(data?.cart_quantity || 0));
        } catch (error) {
            renderExcelImportResult({
                success_count: 0,
                failed_count: 1,
                added_qty: 0,
                failures: [{ row: 0, code: '', reason: 'Không thêm được danh sách đã nhập' }],
            });
        } finally {
            toggleImportButton(quickCartImportButton, false);
        }
    });

    filterBoxButtons.forEach((button) => {
        const box = button.closest('.oms_filter_box');
        if (box?.classList.contains('expended')) {
            box.classList.add('expanded');
            box.classList.remove('expended');
        }
        button.setAttribute('aria-expanded', box?.classList.contains('expanded') ? 'true' : 'false');
        button.addEventListener('click', () => {
            const isExpanded = box?.classList.toggle('expanded') || false;
            button.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
        });
    });

    viewButtons.forEach((button, index) => {
        button.addEventListener('click', () => {
            viewButtons.forEach((item) => item.classList.remove('active'));
            button.classList.add('active');
            productGrid?.classList.toggle('oms_list_view', index === 1);
        });
    });

    applyFilter();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initOmsShop();
    });
} else {
    initOmsShop();
}
