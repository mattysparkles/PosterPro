export function canonicalGroupTone(groupIndex) {
  const index = Number(groupIndex);
  return Number.isFinite(index) && index > 0 && index % 2 === 1 ? "dark" : "light";
}

export function groupPalette(tone) {
  return tone === "dark"
    ? { backgroundColor: "#292929", color: "#ffffff", controlBackground: "#373737", controlColor: "#ffffff", controlBorder: "#a3a3a3" }
    : { backgroundColor: "#e5e7eb", color: "#111827", controlBackground: "#ffffff", controlColor: "#111827", controlBorder: "#9ca3af" };
}

export function slatePalette(role) {
  return role === "TAIL"
    ? { backgroundColor: "#ff00d4", borderColor: "#ff00d4", color: "#170015" }
    : { backgroundColor: "#39ff14", borderColor: "#00c853", color: "#071500" };
}
