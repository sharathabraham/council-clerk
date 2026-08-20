// Shared helpers used by every page. Loads the JSON files the scraper writes to data/.

async function loadData() {
  const [meetings, resolutions, members] = await Promise.all([
    fetch("data/meetings.json").then((r) => r.json()),
    fetch("data/resolutions.json").then((r) => r.json()),
    fetch("data/members.json").then((r) => r.json()),
  ]);
  return { meetings, resolutions, members };
}

function lastName(fullName) {
  const parts = fullName.trim().split(/\s+/);
  return parts[parts.length - 1];
}

function formatDate(isoDate) {
  const [year, month, day] = isoDate.split("-").map(Number);
  const d = new Date(year, month - 1, day);
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function voteBadge(vote) {
  const label = vote || "Absent";
  return `<span class="vote-badge vote-${label}">${label}</span>`;
}

function meetingById(meetings, id) {
  return meetings.find((m) => m.id === id);
}

function memberBySlug(members, slug) {
  return members.find((m) => m.slug === slug);
}

function videoEmbedHtml(videoUrl) {
  if (!videoUrl) return "";
  return `<div class="video-embed"><iframe src="${videoUrl}" allowfullscreen></iframe></div>`;
}

function qs(param) {
  return new URLSearchParams(window.location.search).get(param);
}
