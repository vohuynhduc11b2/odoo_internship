/** @odoo-module **/

import { xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { notificationService } from "@web/core/notifications/notification_service";
import { NotificationContainer } from "@web/core/notifications/notification_container";
import { CartNotification } from "@website_sale_custom/js/notification/cart_notification/cart_notification";

export class CartNotificationContainer extends NotificationContainer {
    static components = {
        ...NotificationContainer.components,
        Notification: CartNotification,
    };

    static template = xml/* xml */ `
        <div class="position-fixed w-100 h-100 top-0 pe-none">
            <div class="d-flex flex-column container align-items-end">
                <t t-foreach="notifications" t-as="notification_value" t-key="notification_value.id || notification_value">
                    <Transition leaveDuration="0" name="'o_notification_fade'">
                        <t t-slot="default" t-slot-scope="transition">
                            <Notification
                                t-props="notification_value.props"
                                className="(notification_value.props?.className || '') + ' ' + (transition?.className || '')"
                            />
                        </t>
                    </Transition>
                </t>
            </div>
        </div>
    `;
}

export const cartNotificationService = {
    ...notificationService,
    notificationContainer: CartNotificationContainer,
};

// Override service (giữ nguyên như bạn làm)
registry
    .category("services")
    .add("cartNotificationService", cartNotificationService, { force: true });
