import { queryOne } from "@odoo/hoot-dom";
import * as Numpad from "@point_of_sale/../tests/generic_helpers/numpad_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";

export function table({ name, withClass = "", withoutClass, run = () => {}, numOfSeats }) {
    let trigger = `.floor-map .table${withClass}`;
    if (withoutClass) {
        trigger += `:not(${withoutClass})`;
    }
    if (name) {
        trigger += `:has(.label:contains("${name}"))`;
    }
    return {
        content: `Check table with attributes: ${JSON.stringify(arguments[0])}`,
        trigger,
        run: typeof run === "string" ? run : () => run(trigger),
    };
}
export const clickTable = (name) => table({ name, run: "click" });
export const hasTable = (name) => table({ name });
export const selectedTableIs = (name) => table({ name, withClass: ".selected" });
export const ctrlClickTable = (name) =>
    table({
        name,
        run: (trigger) => {
            queryOne(trigger).dispatchEvent(
                new MouseEvent("click", { bubbles: true, ctrlKey: true })
            );
        },
    });
export function clickFloor(name) {
    return [
        {
            content: `click '${name}' floor`,
            trigger: `.floor-selector .button-floor:contains("${name}")`,
            run: "click",
        },
    ];
}
export function clickEditButton(button) {
    return [
        {
            content: "add table",
            trigger: `.edit-buttons i[aria-label="${button}"]`,
            run: "click",
        },
    ];
}
export function clickSaveEditButton() {
    return [
        {
            content: "add table",
            trigger: '.edit-buttons button:contains("Save")',
            run: "click",
        },
        {
            trigger: negate(".edit-buttons button:contains('Save')"),
        },
    ];
}
export function clickTableSelectorButton() {
    return [
        {
            content: "click on table selector button",
            trigger: ".floor-screen .right-buttons button.btn-secondary",
            run: "click",
        },
    ];
}
export function goTo(name) {
    return [
        ...clickTableSelectorButton(),
        ...Numpad.enterValue(name),
        {
            trigger: ".floor-screen .right-buttons .jump-button",
            run: "click",
        },
    ];
}
export function selectedFloorIs(name) {
    return [
        {
            content: `selected floor is '${name}'`,
            trigger: `.button-floor.active:contains("${name}")`,
        },
    ];
}
export function orderCountSyncedInTableIs(table, count) {
    if (count === 0 || count === "0") {
        return [
            {
                trigger: `.floor-map .table:has(.label:contains("${table}")):not(:has(.order-count))`,
            },
        ];
    }
    return [
        {
            trigger: `.floor-map .table:has(.label:contains("${table}")):has(.order-count:contains("${count}"))`,
        },
    ];
}
export function isShown() {
    return [
        {
            trigger: ".floor-map",
        },
    ];
}
export function linkTables(child, parent) {
    return {
        content: `Drag table ${child} onto table ${parent} in order to link them`,
        trigger: table({ name: child }).trigger,
        async run(helpers) {
            helpers.delay = 500;
            await helpers.drag_multiple_and_then_drop(
                [
                    table({ name: parent }).trigger,
                    {
                        position: "top",
                        relative: true,
                    },
                ],
                [
                    table({ name: parent }).trigger,
                    {
                        position: "center",
                        relative: true,
                    },
                ]
            );
        },
    };
}
export function unlinkTables(child, parent) {
    return {
        content: `Drag table ${child} away from table ${parent} to unlink them`,
        trigger: table({ name: child }).trigger,
        async run(helpers) {
            await helpers.drag_and_drop(`div.floor-map`, {
                position: {
                    bottom: 0,
                },
                relative: true,
            });
        },
    };
}
export function isChildTable(child) {
    return {
        content: `Verify that table ${child} is a child table`,
        trigger: table({ name: child }).trigger + ` .info.opacity-25`,
    };
}
export function clickNewOrder() {
    return { trigger: ".new-order", run: "click" };
}

export function addFloor(floorName) {
    return [
        {
            trigger: ".floor-selector button i[aria-label='Add Floor']",
            run: "click",
        },
        {
            trigger: ".modal-body textarea",
            run: `edit ${floorName}`,
        },
        {
            trigger: ".modal-footer button.btn-primary",
            run: "click",
        },
        ...selectedFloorIs(floorName),
    ];
}

import { TourHelpers } from "@web_tour/js/tour_automatic/tour_helpers";
import { patch } from "@web/core/utils/patch";
import * as hoot from "@odoo/hoot-dom";

patch(TourHelpers.prototype, {
    async drag_multiple_and_then_drop(...drags) {
        const dragEffectDelay = async () => {
            console.log(this.delay);
            await new Promise((resolve) => requestAnimationFrame(resolve));
            await new Promise((resolve) => setTimeout(resolve, this.delay));
        };
        const element = this.anchor;
        const { drop, moveTo } = await hoot.drag(element);
        await dragEffectDelay();
        await hoot.hover(element, {
            position: {
                top: 20,
                left: 20,
            },
            relative: true,
        });
        await dragEffectDelay();
        for (const [selector, options] of drags) {
            console.log("Selector", selector, options);
            const target = await hoot.waitFor(selector, {
                visible: true,
                timeout: 500,
            });
            await moveTo(target, options);
            await dragEffectDelay();
        }
        await drop();
        await dragEffectDelay();
    },
});
