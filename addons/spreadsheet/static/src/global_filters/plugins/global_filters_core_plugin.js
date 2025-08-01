/** @ts-check */

import { CommandResult } from "@spreadsheet/o_spreadsheet/cancelled_reason";
import {
    checkFilterDefaultValueIsValid,
    globalFieldMatchingRegistry,
} from "@spreadsheet/global_filters/helpers";
import { _t } from "@web/core/l10n/translation";
import { escapeRegExp } from "@web/core/utils/strings";
import { OdooCorePlugin } from "@spreadsheet/plugins";

/**
 * @typedef {import("@spreadsheet").GlobalFilter} GlobalFilter
 * @typedef {import("@spreadsheet").CmdGlobalFilter} CmdGlobalFilter
 * @typedef {import("@spreadsheet").FieldMatching} FieldMatching
 */

export class GlobalFiltersCorePlugin extends OdooCorePlugin {
    static getters = /** @type {const} */ ([
        "getGlobalFilter",
        "getGlobalFilters",
        "getGlobalFilterDefaultValue",
        "getGlobalFilterLabel",
        "getFieldMatchingForModel",
    ]);
    constructor(config) {
        super(config);
        /** @type {Array.<GlobalFilter>} */
        this.globalFilters = [];
    }

    /**
     * Check if the given command can be dispatched
     *
     * @param {import("@spreadsheet").AllCoreCommand} cmd Command
     */
    allowDispatch(cmd) {
        switch (cmd.type) {
            case "EDIT_GLOBAL_FILTER":
                if (!this.getGlobalFilter(cmd.filter.id)) {
                    return CommandResult.FilterNotFound;
                } else if (!cmd.filter.label) {
                    return CommandResult.InvalidFilterLabel;
                } else if (this._isDuplicatedLabel(cmd.filter.id, cmd.filter.label)) {
                    return CommandResult.DuplicatedFilterLabel;
                }
                if (!checkFilterDefaultValueIsValid(cmd.filter, cmd.filter.defaultValue)) {
                    return CommandResult.InvalidValueTypeCombination;
                }
                break;
            case "REMOVE_GLOBAL_FILTER":
                if (!this.getGlobalFilter(cmd.id)) {
                    return CommandResult.FilterNotFound;
                }
                break;
            case "ADD_GLOBAL_FILTER":
                if (!cmd.filter.label) {
                    return CommandResult.InvalidFilterLabel;
                } else if (this._isDuplicatedLabel(cmd.filter.id, cmd.filter.label)) {
                    return CommandResult.DuplicatedFilterLabel;
                }
                if (!checkFilterDefaultValueIsValid(cmd.filter, cmd.filter.defaultValue)) {
                    return CommandResult.InvalidValueTypeCombination;
                }
                break;
            case "MOVE_GLOBAL_FILTER": {
                const index = this.globalFilters.findIndex((filter) => filter.id === cmd.id);
                if (index === -1) {
                    return CommandResult.FilterNotFound;
                }
                const targetIndex = index + cmd.delta;
                if (targetIndex < 0 || targetIndex >= this.globalFilters.length) {
                    return CommandResult.InvalidFilterMove;
                }
                break;
            }
        }
        return CommandResult.Success;
    }

    /**
     * Handle a spreadsheet command
     *
     * @param {Object} cmd Command
     */
    handle(cmd) {
        switch (cmd.type) {
            case "ADD_GLOBAL_FILTER": {
                const filter = { ...cmd.filter };
                if (filter.type === "text" && filter.rangesOfAllowedValues?.length) {
                    filter.rangesOfAllowedValues = filter.rangesOfAllowedValues.map((rangeData) =>
                        this.getters.getRangeFromRangeData(rangeData)
                    );
                }
                this.history.update("globalFilters", [...this.globalFilters, filter]);
                break;
            }
            case "EDIT_GLOBAL_FILTER": {
                this._editGlobalFilter(cmd.filter);
                break;
            }
            case "REMOVE_GLOBAL_FILTER": {
                const filters = this.globalFilters.filter((filter) => filter.id !== cmd.id);
                this.history.update("globalFilters", filters);
                break;
            }
            case "MOVE_GLOBAL_FILTER":
                this._onMoveFilter(cmd.id, cmd.delta);
                break;
        }
    }

    adaptRanges(applyChange) {
        for (const filterIndex in this.globalFilters) {
            const filter = this.globalFilters[filterIndex];
            if (filter.type === "text" && filter.rangesOfAllowedValues) {
                const ranges = filter.rangesOfAllowedValues
                    .map((range) => {
                        const change = applyChange(range);
                        switch (change.changeType) {
                            case "RESIZE":
                            case "MOVE":
                            case "CHANGE": {
                                return change.range;
                            }
                        }
                    })
                    .filter(Boolean);
                this.history.update(
                    "globalFilters",
                    filterIndex,
                    "rangesOfAllowedValues",
                    ranges.length ? ranges : undefined
                );
            }
        }
    }

    // ---------------------------------------------------------------------
    // Getters
    // ---------------------------------------------------------------------

    /**
     * Retrieve the global filter with the given id
     *
     * @param {string} id
     * @returns {GlobalFilter|undefined} Global filter
     */
    getGlobalFilter(id) {
        return this.globalFilters.find((filter) => filter.id === id);
    }

    /**
     * Get the global filter with the given name
     *
     * @param {string} label Label
     *
     * @returns {GlobalFilter|undefined}
     */
    getGlobalFilterLabel(label) {
        return this.globalFilters.find((filter) => _t(filter.label) === _t(label));
    }

    /**
     * Retrieve all the global filters
     *
     * @returns {Array<GlobalFilter>} Array of Global filters
     */
    getGlobalFilters() {
        return [...this.globalFilters];
    }

    /**
     * Get the default value of a global filter
     *
     * @param {string} id Id of the filter
     *
     * @returns {string|Array<string>|Object}
     */
    getGlobalFilterDefaultValue(id) {
        return this.getGlobalFilter(id).defaultValue;
    }

    /**
     * Returns the field matching for a given model by copying the matchings of another DataSource that
     * share the same model, including only the chain and type.
     *
     * @returns {Record<string, FieldMatching> | {}}
     */
    getFieldMatchingForModel(newModel) {
        const globalFilters = this.getGlobalFilters();
        if (globalFilters.length === 0) {
            return {};
        }

        for (const matcher of globalFieldMatchingRegistry.getAll()) {
            for (const dataSourceId of matcher.getIds(this.getters)) {
                const model = matcher.getModel(this.getters, dataSourceId);
                if (model === newModel) {
                    const fieldMatching = {};
                    for (const filter of globalFilters) {
                        const matchedField = matcher.getFieldMatching(
                            this.getters,
                            dataSourceId,
                            filter.id
                        );
                        if (matchedField) {
                            fieldMatching[filter.id] = {
                                chain: matchedField.chain,
                                type: matchedField.type,
                            };
                        }
                    }
                    return fieldMatching;
                }
            }
        }
        return {};
    }

    // ---------------------------------------------------------------------
    // Handlers
    // ---------------------------------------------------------------------

    /**
     * Edit a global filter
     *
     * @param {CmdGlobalFilter} cmdFilter
     */
    _editGlobalFilter(cmdFilter) {
        const rangesOfAllowedValues =
            cmdFilter.type === "text" && cmdFilter.rangesOfAllowedValues?.length
                ? cmdFilter.rangesOfAllowedValues.map((rangeData) =>
                      this.getters.getRangeFromRangeData(rangeData)
                  )
                : undefined;
        /** @type {GlobalFilter} */
        const newFilter =
            cmdFilter.type === "text" ? { ...cmdFilter, rangesOfAllowedValues } : { ...cmdFilter };
        const id = newFilter.id;
        const currentLabel = this.getGlobalFilter(id).label;
        const index = this.globalFilters.findIndex((filter) => filter.id === id);
        if (index === -1) {
            return;
        }
        this.history.update("globalFilters", index, newFilter);
        const newLabel = this.getGlobalFilter(id).label;
        if (currentLabel !== newLabel) {
            this._updateFilterLabelInFormulas(currentLabel, newLabel);
        }
    }

    // ---------------------------------------------------------------------
    // Import/Export
    // ---------------------------------------------------------------------

    /**
     * Import the filters
     *
     * @param {Object} data
     */
    import(data) {
        for (const globalFilter of data.globalFilters || []) {
            if (globalFilter.type === "text" && globalFilter.rangesOfAllowedValues?.length) {
                globalFilter.rangesOfAllowedValues = globalFilter.rangesOfAllowedValues.map((xc) =>
                    this.getters.getRangeFromSheetXC(
                        // The default sheet id doesn't matter here, the exported range string
                        // is fully qualified and contains the sheet name.
                        // The getter expects a valid sheet id though, let's give it the
                        // first sheet id.
                        data.sheets[0].id,
                        xc
                    )
                );
            }
            this.globalFilters.push(globalFilter);
        }
    }
    /**
     * Export the filters
     *
     * @param {Object} data
     */
    export(data) {
        data.globalFilters = this.globalFilters.map((filter) => {
            /** @type {Object} */
            const filterData = { ...filter };
            if (filter.type === "text" && filter.rangesOfAllowedValues?.length) {
                filterData.rangesOfAllowedValues = filter.rangesOfAllowedValues.map((range) =>
                    this.getters.getRangeString(
                        range,
                        "" // force the range string to be fully qualified (with the sheet name)
                    )
                );
            }
            return filterData;
        });
    }

    // ---------------------------------------------------------------------
    // Global filters
    // ---------------------------------------------------------------------

    /**
     * Update all ODOO.FILTER.VALUE formulas to reference a filter
     * by its new label.
     *
     * @param {string} currentLabel
     * @param {string} newLabel
     */
    _updateFilterLabelInFormulas(currentLabel, newLabel) {
        const sheetIds = this.getters.getSheetIds();
        currentLabel = escapeRegExp(currentLabel);
        for (const sheetId of sheetIds) {
            for (const cell of Object.values(this.getters.getCells(sheetId))) {
                if (cell.isFormula) {
                    const newContent = cell.content.replace(
                        new RegExp(`FILTER\\.VALUE\\(\\s*"${currentLabel}"\\s*\\)`, "g"),
                        `FILTER.VALUE("${newLabel}")`
                    );
                    if (newContent !== cell.content) {
                        const { col, row } = this.getters.getCellPosition(cell.id);
                        this.dispatch("UPDATE_CELL", {
                            sheetId,
                            content: newContent,
                            col,
                            row,
                        });
                    }
                }
            }
        }
    }

    /**
     * Return true if the label is duplicated
     *
     * @param {string | undefined} filterId
     * @param {string} label
     * @returns {boolean}
     */
    _isDuplicatedLabel(filterId, label) {
        return (
            this.globalFilters.findIndex(
                (filter) => (!filterId || filter.id !== filterId) && filter.label === label
            ) > -1
        );
    }

    _onMoveFilter(filterId, delta) {
        const filters = [...this.globalFilters];
        const currentIndex = filters.findIndex((s) => s.id === filterId);
        const filter = filters[currentIndex];
        const targetIndex = currentIndex + delta;

        filters.splice(currentIndex, 1);
        filters.splice(targetIndex, 0, filter);

        this.history.update("globalFilters", filters);
    }
}
