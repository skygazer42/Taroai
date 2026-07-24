export function resolveAccountIdentity(displayName = "", email = "") {
  const name = displayName.trim() || email.trim().split("@")[0] || "User";
  const parts = name.split(/\s+/);
  const firstName = Array.from(parts[0]);
  const initials = parts
    .slice(0, 2)
    .map((part) => Array.from(part)[0])
    .join("")
    .toUpperCase();
  const shortName = /^\d+$/.test(parts[0])
    ? firstName.slice(0, 4).join("")
    : firstName.length > 16
      ? `${firstName.slice(0, 15).join("")}…`
      : parts[0];
  return { name, shortName, initials };
}

export function resolveGreetingFontSize(name = "") {
  return Math.max(44, 72 - Math.max(0, Array.from(name).length - 4) * 3);
}
