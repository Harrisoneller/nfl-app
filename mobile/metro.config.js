// Default Expo Metro config. Kept explicit so it's easy to extend later
// (e.g. SVG transformer, monorepo watchFolders).
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

// Use Node fs watcher instead of Watchman (Watchman hangs on this machine)
config.watchFolders = [];
config.watcher = {
  watchman: { deferStates: [] },
  additionalExts: ["cjs", "mjs"],
};

module.exports = config;
