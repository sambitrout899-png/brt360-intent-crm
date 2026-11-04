import { fetchBlob, request } from "./client";

/** Admin -> Report Design: how the organization's PDF reports look (backend app/report_design.py). */
export type ReportDesignSettings = {
  logo: "app" | "custom" | "none";
  logo_data_url: string | null;
  logo_position: "left" | "right";
  accent: string; // "" = the app's brand colour, else the BRT 360 blue
  header_style: "band" | "tint" | "clean";
  company_name: string;
  tagline: string;
  footer_text: string;
  confidentiality: string;
  watermark: string;
  show_generated: boolean;
  page_size: "A4" | "Letter";
};

export type ReportDesign = {
  settings: ReportDesignSettings;
  customized: boolean;
  app_logo_data_url: string | null;
  app_brand_color: string | null;
  organization: string;
  effective: { company: string; accent: string; logo: boolean; logo_note: string };
  header_styles: { key: ReportDesignSettings["header_style"]; label: string }[];
  page_sizes: string[];
  placeholders: string[];
};

const BASE = "/api/admin/report-design";

export const getReportDesign = () => request<ReportDesign>(BASE);
export const saveReportDesign = (settings: ReportDesignSettings) => request<ReportDesign>(BASE, { method: "PUT", body: JSON.stringify(settings) });
export const resetReportDesign = () => request<ReportDesign>(BASE, { method: "DELETE" });
/** A sample report with these (unsaved) settings. */
export const previewReportDesign = (settings: ReportDesignSettings) => fetchBlob(`${BASE}/preview`, { method: "POST", body: settings });
