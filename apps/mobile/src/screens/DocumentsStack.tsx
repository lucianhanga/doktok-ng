import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";

import { DocumentsScreen } from "./DocumentsScreen";
import { DocumentDetailScreen } from "./DocumentDetailScreen";
import { colors, spacing, typeScale } from "../theme";

export type DocumentsStackParamList = {
  DocumentsList: undefined;
  DocumentDetail: { id: string };
};

const Stack = createNativeStackNavigator<DocumentsStackParamList>();

export function DocumentsStack() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTitleStyle: { color: colors.text },
        headerTintColor: colors.accent,
        headerShown: true,
      }}
    >
      <Stack.Screen name="DocumentsList" options={{ title: "Documents", headerShown: false }}>
        {({ navigation }) => (
          <DocumentsScreen
            onOpenDocument={(id) => navigation.navigate("DocumentDetail", { id })}
          />
        )}
      </Stack.Screen>
      <Stack.Screen name="DocumentDetail" options={{ title: "Document" }}>
        {({ route }) => <DocumentDetailScreen id={route.params.id} />}
      </Stack.Screen>
    </Stack.Navigator>
  );
}
