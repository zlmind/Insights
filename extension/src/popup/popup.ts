import { initPairingView } from "../shared/pairing-view";

// Clicking the toolbar icon shows the pairing view directly — no dead icon.
initPairingView();

document.getElementById("open-options")?.addEventListener("click", (e) => {
  e.preventDefault();
  void chrome.runtime.openOptionsPage();
});
