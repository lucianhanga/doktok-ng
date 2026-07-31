import React from "react";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer, DarkTheme, Theme } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { AuthProvider, useAuth } from "./src/auth/AuthContext";
import { LoginScreen } from "./src/screens/LoginScreen";
import { DocumentsStack } from "./src/screens/DocumentsStack";
import { ScanScreen } from "./src/screens/ScanScreen";
import { ChatStack } from "./src/screens/ChatStack";
import { InsightsScreen } from "./src/screens/InsightsScreen";
import { ActivityScreen } from "./src/screens/ActivityScreen";
import { IngestionTrackerProvider } from "./src/scan/tracker";
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

function Gate() {
  const { status, signOut } = useAuth();

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
        {(["Documents", "Scan", "Chat", "Insights", "Activity"] as const).map((name) => (
          <Tab.Screen key={name} name={name} options={{ title: name }}>
            {() => {
              if (name === "Documents") return <DocumentsStack />;
              if (name === "Scan") return <ScanScreen />;
              if (name === "Chat") return <ChatStack />;
              if (name === "Insights") return <InsightsScreen />;
              return <ActivityScreen />;
            }}
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
        <IngestionTrackerProvider>
          <Gate />
        </IngestionTrackerProvider>
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
