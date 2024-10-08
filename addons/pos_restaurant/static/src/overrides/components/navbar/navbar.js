import { Navbar } from "@point_of_sale/app/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { makeAwaitable } from "@point_of_sale/app/store/make_awaitable_dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { NumberPopup } from "@point_of_sale/app/utils/input_popups/number_popup";
import {
    getButtons,
    EMPTY,
    ZERO,
    BACKSPACE,
} from "@point_of_sale/app/generic_components/numpad/numpad";

patch(Navbar.prototype, {
    /**
     * If no table is set to pos, which means the current main screen
     * is floor screen, then the order count should be based on all the orders.
     */

    get orderCount() {
        if (this.pos.config.module_pos_restaurant && this.pos.selectedTable) {
            return this.pos.getTableOrders(this.pos.selectedTable.id).length;
        }
        return super.orderCount;
    },
    getTable() {
        return this.pos.orderToTransferUuid
            ? this.pos.models["pos.order"].find((o) => o.uuid == this.pos.orderToTransferUuid)
                  ?.table_id
            : this.pos.selectedTable;
    },
    showTabs() {
        if (this.pos.config.module_pos_restaurant) {
            return !(this.pos.selectedTable || this.pos.orderToTransferUuid);
        } else {
            return super.showTabs();
        }
    },
    getFloatingOrders() {
        const draftOrders = super.getFloatingOrders() || [];
        return draftOrders.filter((o) => !o.table_id);
    },
    onSwitchButtonClick() {
        const mode = this.pos.floorPlanStyle === "kanban" ? "default" : "kanban";
        localStorage.setItem("floorPlanStyle", mode);
        this.pos.floorPlanStyle = mode;
    },
    get showEditPlanButton() {
        return true;
    },
    async getTableOrFloatingOrder() {
        const orderToTransfer = this.pos.models["pos.order"].getBy(
            "uuid",
            this.pos.orderToTransferUuid
        );
        if (orderToTransfer) {
            return [this.getTable(), orderToTransfer];
        }
        const table_number = await makeAwaitable(this.dialog, NumberPopup, {
            title: _t("Table Selector"),
            placeholder: _t("Enter a table number"),
            buttons: getButtons([
                EMPTY,
                ZERO,
                { ...BACKSPACE, class: "o_colorlist_item_color_transparent_1" },
            ]),
            confirmButtonLabel: _t("Jump to table"),
        });
        if (!table_number) {
            return [null, null];
        }
        const find_table = (t) => t.table_number === parseInt(table_number);
        let table = this.pos.currentFloor?.table_ids.find(find_table);
        if (!table) {
            table = this.pos.models["restaurant.table"].find(find_table);
        }
        let floating_order;
        if (!table) {
            floating_order = this.getFloatingOrders().find(
                (o) => o.getFloatingOrderName() === table_number
            );
        }
        return [table, floating_order];
    },
    async switchTable() {
        const [table, floating_order] = await this.getTableOrFloatingOrder();
        if (!table && !floating_order) {
            this.dialog.add(AlertDialog, {
                title: _t("Error"),
                body: _t("No table or floating order found with this number"),
            });
            return;
        }
        this.pos.selectedTable = null;
        this.pos.searchProductWord = "";
        if (table) {
            await this.pos.setTableFromUi(table);
        } else {
            this.selectFloatingOrder(floating_order);
            this.pos.orderToTransferUuid = null;
        }
    },
    getOrderToDisplay() {
        const currentOrder = this.pos.get_order();
        const orderToTransfer = this.pos.models["pos.order"].find((order) => {
            return order.uuid === this.pos.orderToTransferUuid;
        });
        return currentOrder || orderToTransfer;
    },
    getOrderName() {
        const order = this.getOrderToDisplay();
        return order.table_id?.table_number || order.getFloatingOrderName();
    },
    onClickPlanButton() {
        this.pos.orderToTransferUuid = null;
        this.pos.showScreen("FloorScreen", { floor: this.floor });
    },
});
