// Minimal ambient declaration for dom-to-pptx — the package ships no types.
// We only use exportToPptx; declare the rest as `any` loosely.

declare module "dom-to-pptx" {
  export interface ExportToPptxOptions {
    fileName?: string;
    skipDownload?: boolean;
    autoEmbedFonts?: boolean;
    svgAsVector?: boolean;
    width?: number;
    height?: number;
    layout?: string;
    listConfig?: Record<string, unknown>;
  }

  export function exportToPptx(
    target: string | HTMLElement | Array<string | HTMLElement>,
    options?: ExportToPptxOptions,
  ): Promise<Blob>;
}
