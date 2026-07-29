// Metro config (apps/mobile): react-native-markdown-display -> markdown-it imports Node's
// "punycode", which the RN runtime lacks; alias it to the userland implementation.
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  punycode: require.resolve("punycode/"),
};

module.exports = config;
