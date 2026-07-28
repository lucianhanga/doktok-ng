import React from "react";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer, DarkTheme, Theme } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PlaceholderScreen } from "./src/screens/PlaceholderScreen";
import { colors } from "./src/theme";

const Tab = createBottomTabNavigator();
const queryClient = new QueryClient();

// React Navigation theme variant of our dark palette (tabs + headers match the web UI).
const navTheme: Theme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: colors.accent,
    background: colors.bg,
    card: colors.surface,
    text: colors.text,
    border: colors.border,
    notification: colors.accent,
  },
};

const TAB_NOTES: Record<string, string> = {
  Documents: "the document list lands in M1.3",
  Scan: "camera scanning lands in M2.1",
  Chat: "chat lands in M3.1",
  Insights: "phone-adapted insights land in M4.1",
  Settings: "settings land later",
};

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <NavigationContainer theme={navTheme}>
        <StatusBar style="light" />
        <Tab.Navigator
          screenOptions={{
            headerStyle: { backgroundColor: colors.surface },
            headerTitleStyle: { color: colors.text },
            tabBarStyle: {
              backgroundColor: colors.surface,
              borderTopColor: colors.border,
            },
            tabBarActiveTintColor: colors.accent,
            tabBarInactiveTintColor: colors.muted,
          }}
        >
          {(["Documents", "Scan", "Chat", "Insights", "Settings"] as const).map((name) => (
            <Tab.Screen
              key={name}
              name={name}
              options={{ title: name === "Scan" ? "Scan" : name }}
            >
              {() => <PlaceholderScreen name={name} note={TAB_NOTES[name]} />}
            </Tab.Screen>
          ))}
        </Tab.Navigator>
      </NavigationContainer>
    </QueryClientProvider>
  );
}
