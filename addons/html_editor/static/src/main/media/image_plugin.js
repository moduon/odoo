import { Plugin } from "../../plugin";
import { _t } from "@web/core/l10n/translation";
import { isImageUrl } from "@html_editor/utils/url";
import { ImageDescription } from "./image_description";
import { ImageToolbarDropdown } from "./image_toolbar_dropdown";
import { createFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { isHtmlContentSupported } from "@html_editor/core/selection_plugin";
import { boundariesOut } from "@html_editor/utils/position";
import { withSequence } from "@html_editor/utils/resource";
import { ImageTransformButton } from "./image_transform_button";
import { callbacksForCursorUpdate } from "@html_editor/utils/selection";
import { closestBlock } from "@html_editor/utils/blocks";
import { fillEmpty } from "@html_editor/utils/dom";
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { ImageTransformation } from "./image_transformation";

function hasShape(imagePlugin, shapeName) {
    return () => imagePlugin.isSelectionShaped(shapeName);
}

export const IMAGE_SHAPES = ["rounded", "rounded-circle", "shadow", "img-thumbnail"];

const IMAGE_PADDING = [
    { name: "None", value: 0 },
    { name: "Small", value: 1 },
    { name: "Medium", value: 2 },
    { name: "Large", value: 3 },
    { name: "XL", value: 5 },
];

const IMAGE_SIZE = [
    { name: "Default", value: "" },
    { name: "100%", value: "100%" },
    { name: "50%", value: "50%" },
    { name: "25%", value: "25%" },
];

export class ImagePlugin extends Plugin {
    static id = "image";
    static dependencies = ["history", "link", "powerbox", "dom", "selection"];
    static shared = ["getTargetedImage", "previewImage"];
    resources = {
        user_commands: [
            {
                id: "deleteImage",
                description: _t("Remove (DELETE) image"),
                icon: "fa-trash text-danger",
                run: this.deleteImage.bind(this),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "previewImage",
                description: _t("Preview image"),
                icon: "fa-search-plus",
                run: this.previewImage.bind(this),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "setImageShapeRounded",
                description: _t("Set shape: Rounded"),
                icon: "fa-square",
                run: () => this.setImageShape("rounded", { excludeClasses: ["rounded-circle"] }),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "setImageShapeCircle",
                description: _t("Set shape: Circle"),
                icon: "fa-circle-o",
                run: () => this.setImageShape("rounded-circle", { excludeClasses: ["rounded"] }),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "setImageShapeShadow",
                description: _t("Set shape: Shadow"),
                icon: "fa-sun-o",
                run: () => this.setImageShape("shadow"),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "setImageShapeThumbnail",
                description: _t("Set shape: Thumbnail"),
                icon: "fa-picture-o",
                run: () => this.setImageShape("img-thumbnail"),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "resizeImage",
                run: this.resizeImage.bind(this),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "transformImage",
                run: this.handleImageTransformation.bind(this),
                isAvailable: isHtmlContentSupported,
            },
        ],
        toolbar_namespaces: [
            {
                id: "image",
                isApplied: (targetedNodes) =>
                    targetedNodes.every(
                        // All nodes should be images or its ancestors
                        (node) => node.nodeName === "IMG" || node.querySelector?.("img")
                    ),
            },
        ],
        toolbar_groups: [
            withSequence(23, { id: "image_preview", namespaces: ["image"] }),
            withSequence(24, { id: "image_description", namespaces: ["image"] }),
            withSequence(25, { id: "image_shape", namespaces: ["image"] }),
            withSequence(26, { id: "image_padding", namespaces: ["image"] }),
            withSequence(26, { id: "image_size", namespaces: ["image"] }),
            withSequence(26, { id: "image_modifiers", namespaces: ["image"] }),
            withSequence(32, { id: "image_delete", namespaces: ["image"] }),
        ],
        toolbar_items: [
            {
                id: "image_preview",
                groupId: "image_preview",
                commandId: "previewImage",
            },
            {
                id: "image_description",
                description: _t("Edit media description"),
                groupId: "image_description",
                Component: ImageDescription,
                props: {
                    getDescription: () => this.getImageAttribute("alt"),
                    getTooltip: () => this.getImageAttribute("title"),
                    updateImageDescription: this.updateImageDescription.bind(this),
                },
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "shape_rounded",
                groupId: "image_shape",
                commandId: "setImageShapeRounded",
                isActive: hasShape(this, "rounded"),
            },
            {
                id: "shape_circle",
                groupId: "image_shape",
                commandId: "setImageShapeCircle",
                isActive: hasShape(this, "rounded-circle"),
            },
            {
                id: "shape_shadow",
                groupId: "image_shape",
                commandId: "setImageShapeShadow",
                isActive: hasShape(this, "shadow"),
            },
            {
                id: "shape_thumbnail",
                groupId: "image_shape",
                commandId: "setImageShapeThumbnail",
                isActive: hasShape(this, "img-thumbnail"),
            },
            {
                id: "image_padding",
                groupId: "image_padding",
                description: _t("Set image padding"),
                Component: ImageToolbarDropdown,
                props: {
                    name: "image_padding",
                    icon: "html_editor.ImagePaddingIcon",
                    items: IMAGE_PADDING,
                    onSelected: (item) => {
                        this.setImagePadding({ size: item.value });
                    },
                },
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "image_size",
                groupId: "image_size",
                description: _t("Resize image"),
                Component: ImageToolbarDropdown,
                props: {
                    name: "image_size",
                    getDisplay: () => this.imageSize,
                    items: IMAGE_SIZE,
                    onSelected: (item) => {
                        this.resizeImage({ size: item.value });
                        this.updateImageParams();
                    },
                },
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "image_transform",
                groupId: "image_modifiers",
                description: _t("Transform the picture (click twice to reset transformation)"),
                Component: ImageTransformButton,
                props: this.getImageTransformProps(),
                isAvailable: isHtmlContentSupported,
            },
            {
                id: "image_delete",
                groupId: "image_delete",
                commandId: "deleteImage",
            },
        ],

        /** Handlers */
        selectionchange_handlers: this.updateImageParams.bind(this),
        post_undo_handlers: this.updateImageParams.bind(this),
        post_redo_handlers: this.updateImageParams.bind(this),

        /** Overrides */
        paste_url_overrides: this.handlePasteUrl.bind(this),
    };

    setup() {
        this.imageSize = reactive({ displayName: "Default" });
        this.transformState = reactive({ active: false });
        this.pointerDownInsideTransform = false;
        this.addDomListener(this.editable, "pointerup", (e) => {
            if (e.target.tagName === "IMG") {
                const [anchorNode, anchorOffset, focusNode, focusOffset] = boundariesOut(e.target);
                this.dependencies.selection.setSelection({
                    anchorNode,
                    anchorOffset,
                    focusNode,
                    focusOffset,
                });
                this.dependencies.selection.focusEditable();
            }
        });
        this.fileViewer = createFileViewer();
        // We close the image transform when we click outside any element not
        // related to it.When the pointerdown of the click is inside the image
        // transform and pointerup is outside while resizing or rotating the
        // image it will consider the click as being done outside image
        // transform.So we need to keep track if the pointerdown is inside or
        // outside to know if we want to close the image transform component or
        // not.
        this.addDomListener(this.document, "pointerdown", (ev) => {
            if (this.isNodeInsideTransform(ev.target)) {
                this.pointerDownInsideTransform = true;
            } else {
                this.closeImageTransformation();
                this.pointerDownInsideTransform = false;
            }
        });
        this.addDomListener(this.document, "click", (ev) => {
            if (!this.isNodeInsideTransform(ev.target) && !this.pointerDownInsideTransform) {
                this.closeImageTransformation();
            }
            this.pointerDownInsideTransform = false;
        });
        // When we click on any character the image is deleted and we need to close the image transform
        // We handle this by selectionchange
        this.addDomListener(this.document, "selectionchange", (ev) => {
            this.closeImageTransformation();
        });
    }

    destroy() {
        super.destroy();
    }

    get imageSizeName() {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return "Default";
        }
        return targetedImg.style.width || "Default";
    }

    setImagePadding({ size } = {}) {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return;
        }
        for (const classString of targetedImg.classList) {
            if (classString.match(/^p-[0-9]$/)) {
                targetedImg.classList.remove(classString);
            }
        }
        targetedImg.classList.add(`p-${size}`);
        this.dependencies.history.addStep();
    }
    resizeImage({ size } = {}) {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return;
        }
        targetedImg.style.width = size || "";
        targetedImg.style.height = size || "";
        this.dependencies.history.addStep();
    }

    handleImageTransformation(image = this.getTargetedImage()) {
        if (this.isImageTransformationOpen()) {
            this.resetImageTransformation(image);
            this.closeImageTransformation();
        } else {
            this.openImageTransformation(image);
        }
    }

    isNodeInsideTransform(node) {
        if (!node) {
            return false;
        }
        if (node.nodeType === Node.TEXT_NODE) {
            node = node.parentElement;
        }
        if (node.matches('[name="image_transform"], [name="image_transform"] *')) {
            return true;
        }
        if (
            this.isImageTransformationOpen() &&
            node.matches(
                ".transfo-container, .transfo-container div, .transfo-container i, .transfo-container span"
            )
        ) {
            return true;
        }
        return false;
    }

    openImageTransformation(image) {
        this.transformState.active = true;
        registry.category("main_components").add("ImageTransformation", {
            Component: ImageTransformation,
            props: {
                image,
                document: this.document,
                editable: this.editable,
                destroy: this.closeImageTransformation.bind(this),
                onChange: this.dependencies.history.addStep.bind(this),
            },
        });
    }

    isImageTransformationOpen() {
        return registry.category("main_components").contains("ImageTransformation");
    }

    closeImageTransformation() {
        this.transformState.active = false;
        if (this.isImageTransformationOpen()) {
            registry.category("main_components").remove("ImageTransformation");
        }
    }

    setImageShape(className, { excludeClasses = [] } = {}) {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return;
        }
        for (const classString of excludeClasses) {
            if (targetedImg.classList.contains(classString)) {
                targetedImg.classList.remove(classString);
            }
        }
        targetedImg.classList.toggle(className);
        this.dependencies.history.addStep();
    }

    previewImage() {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return;
        }
        let imageName;
        // Keep the result from the first predicate that returns something.
        this.getResource("image_name_predicates").find((p) => {
            imageName = p(targetedImg);
            return imageName;
        });
        const fileModel = {
            isImage: true,
            isViewable: true,
            name: imageName || targetedImg.src,
            defaultSource: targetedImg.src,
            downloadUrl: targetedImg.src,
        };
        this.document.getSelection().collapseToEnd();
        this.fileViewer.open(fileModel);
    }

    deleteImage() {
        const targetedImg = this.getTargetedImage();
        if (targetedImg) {
            if (this.delegateTo("delete_image_overrides", targetedImg)) {
                return;
            }
            const cursors = this.dependencies.selection.preserveSelection();
            cursors.update(callbacksForCursorUpdate.remove(targetedImg));
            const parentEl = closestBlock(targetedImg);
            targetedImg.remove();
            cursors.restore();
            fillEmpty(parentEl);
            this.dependencies.history.addStep();
        }
    }

    getTargetedImage() {
        const targetedNodes = this.dependencies.selection.getTargetedNodes();
        return targetedNodes.find((node) => node.tagName === "IMG");
    }

    hasImageSize(size) {
        const targetedImg = this.getTargetedImage();
        return targetedImg?.style?.width === size;
    }

    isSelectionShaped(shape) {
        const targetedNodes = this.dependencies.selection
            .getTargetedNodes()
            .filter((n) => n.tagName === "IMG" && n.classList.contains(shape));
        return targetedNodes.length > 0;
    }

    getImageAttribute(attributeName) {
        const targetedNodes = this.dependencies.selection.getTargetedNodes();
        const targetedImg = targetedNodes.find((node) => node.tagName === "IMG");
        return targetedImg.getAttribute(attributeName) || undefined;
    }

    /**
     * @param {string} text
     * @param {string} url
     */
    handlePasteUrl(text, url) {
        if (isImageUrl(url)) {
            const restoreSavepoint = this.dependencies.history.makeSavePoint();
            // Open powerbox with commands to embed media or paste as link.
            // Insert URL as text, revert it later if a command is triggered.
            this.dependencies.dom.insert(text);
            this.dependencies.history.addStep();
            const embedImageCommand = {
                title: _t("Embed Image"),
                description: _t("Embed the image in the document."),
                icon: "fa-image",
                run: () => {
                    const img = document.createElement("IMG");
                    img.setAttribute("src", url);
                    this.dependencies.dom.insert(img);
                    this.dependencies.history.addStep();
                },
            };
            const commands = [
                embedImageCommand,
                this.dependencies.link.getPathAsUrlCommand(text, url),
            ];
            this.dependencies.powerbox.openPowerbox({ commands, onApplyCommand: restoreSavepoint });
            return true;
        }
    }

    updateImageDescription({ description, tooltip } = {}) {
        const targetedImg = this.getTargetedImage();
        if (!targetedImg) {
            return;
        }
        targetedImg.setAttribute("alt", description);
        targetedImg.setAttribute("title", tooltip);
        this.dependencies.history.addStep();
    }

    resetImageTransformation(image) {
        image.setAttribute(
            "style",
            (image.getAttribute("style") || "").replace(/[^;]*transform[\w:]*;?/g, "")
        );
        image.style.removeProperty("width");
        image.style.removeProperty("height");
        this.dependencies.history.addStep();
    }

    getImageTransformProps() {
        return {
            id: "image_transform",
            icon: "fa-object-ungroup",
            title: _t("Transform the picture (click twice to reset transformation)"),
            activeTitle: _t("Click again to reset transformation"),
            handleImageTransformation: this.handleImageTransformation.bind(this),
            getTransformState: () => this.transformState,
        };
    }

    updateImageParams() {
        this.imageSize.displayName = this.imageSizeName;
    }
}
