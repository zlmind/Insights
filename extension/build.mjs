import * as esbuild from "esbuild";
import { cpSync, mkdirSync } from "node:fs";

mkdirSync("dist/content", { recursive: true });
mkdirSync("dist/options", { recursive: true });
mkdirSync("dist/popup", { recursive: true });
mkdirSync("dist/lib", { recursive: true });
const common = { bundle: true, target: "chrome120", logLevel: "info" };
await esbuild.build({ ...common, entryPoints: ["src/background/service-worker.ts"],
                      outfile: "dist/service-worker.js", format: "esm" });
await esbuild.build({ ...common, entryPoints: ["src/content/bridge.ts"],
                      outfile: "dist/content/bridge.js", format: "iife" });
await esbuild.build({ ...common, entryPoints: ["src/options/options.ts"],
                      outfile: "dist/options/options.js", format: "iife" });
await esbuild.build({ ...common, entryPoints: ["src/popup/popup.ts"],
                      outfile: "dist/popup/popup.js", format: "iife" });
// The protocol gate drives this bundle under node against a real HTTP server.
await esbuild.build({ ...common, entryPoints: ["src/lib/protocol.ts"],
                      outfile: "dist/lib/protocol.js", format: "esm" });
cpSync("src/manifest.json", "dist/manifest.json");
cpSync("src/options/options.html", "dist/options/options.html");
