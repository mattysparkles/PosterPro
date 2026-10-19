export function canonicalGroupTone(groupIndex) {
  const index = Number(groupIndex);
  return Number.isFinite(index) && index > 0 && index % 2 === 1 ? "dark" : "light";
}

export function groupPalette(tone) {
  return tone === "dark"
    ? { backgroundColor: "#f1f5f9", color: "#0f172a", controlBackground: "#ffffff", controlColor: "#0f172a", controlBorder: "#cbd5e1" }
    : { backgroundColor: "#fffaf2", color: "#1f2937", controlBackground: "#ffffff", controlColor: "#1f2937", controlBorder: "#cbd5e1" };
}

export function slatePalette(role) {
  return role === "TAIL"
    ? { backgroundColor: "#00FF00", borderColor: "#087f23", color: "#052e16" }
    : { backgroundColor: "#00FF00", borderColor: "#087f23", color: "#052e16" };
}
