import React from "react";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer, DarkTheme, Theme } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { AuthProvider, useAuth } from "./src/auth/AuthContext";
import { LoginScreen } from "./src/screens/LoginScreen";
import { PlaceholderScreen } from "./src/screens/PlaceholderScreen";
import { colors, spacing, typeScale } from "./src/theme";

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

function Gate() {
  const { status, user, signOut } = useAuth();

  if (status === "loading") {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }
  if (status === "signedOut") {
    return <LoginScreen />;
  }
  return (
    <NavigationContainer theme={navTheme}>
      <StatusBar style="light" />
      <Tab.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTitleStyle: { color: colors.text },
          headerRight: () => (
            <TouchableOpacity onPress={signOut} style={styles.signOut}>
              <Text style={styles.signOutText}>sign out</Text>
            </TouchableOpacity>
          ),
          tabBarStyle: {
            backgroundColor: colors.surface,
            borderTopColor: colors.border,
          },
          tabBarActiveTintColor: colors.accent,
          tabBarInactiveTintColor: colors.muted,
        }}
      >
        {(["Documents", "Scan", "Chat", "Insights", "Settings"] as const).map((name) => (
          <Tab.Screen key={name} name={name} options={{ title: name }}>
            {() => (
              <PlaceholderScreen
                name={`${name}${user ? ` (${user.display_name || user.email})` : ""}`}
                note={TAB_NOTES[name]}
              />
            )}
          </Tab.Screen>
        ))}
      </Tab.Navigator>
    </NavigationContainer>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
  signOut: { marginRight: spacing.md },
  signOutText: { ...typeScale.muted, color: colors.accent },
});
