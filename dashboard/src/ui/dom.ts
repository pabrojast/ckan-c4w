/** Todo el DOM del dashboard se resuelve dentro del root: dos instancias en
 *  la misma página no pueden pisarse porque nunca se consulta `document`. */
export function role<T extends Element = HTMLElement>(root: ParentNode, name: string): T {
  const el = root.querySelector<T>(`[data-role="${name}"]`);
  if (!el) throw new Error(`c4w-dashboard: missing element [data-role="${name}"]`);
  return el;
}

export function roleOrNull<T extends Element = HTMLElement>(root: ParentNode, name: string): T | null {
  return root.querySelector<T>(`[data-role="${name}"]`);
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}
