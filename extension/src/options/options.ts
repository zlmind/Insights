// The options page is the full pairing view; the shared module owns the
// wiring and the pure helpers the tests import.
export { normalizePairCode, originPattern, statusText } from "../shared/pairing-view";
import { initPairingView } from "../shared/pairing-view";

// Page wiring only runs where a DOM exists; the unit tests import the pure
// helpers from a node environment.
if (typeof document !== "undefined") {
  initPairingView();
}
