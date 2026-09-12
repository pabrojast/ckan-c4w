import type { StyleSpecification } from "maplibre-gl";
import { DEFAULT_BASEMAP } from "../config";
import positronBorderless from "../../../ckanext/c4w/public/c4w/maps/positron-borderless.json";

/** Share the project editor's borderless style, with a separate copy per map. */
export function basemapStyle(url: string): string | StyleSpecification {
  if (url && url !== DEFAULT_BASEMAP) return url;
  return JSON.parse(JSON.stringify(positronBorderless)) as StyleSpecification;
}
