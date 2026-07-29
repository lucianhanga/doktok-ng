import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";

import { DocumentsScreen } from "./DocumentsScreen";
import { colors, spacing, typeScale } from "../theme";

export type DocumentsStackParamList = {
  DocumentsList: undefined;
  DocumentDetail: { id: string };
};

const Stack = createNativeStackNavigator<DocumentsStackParamList>();

// Stub until M1.4 (#773): lets the list navigate without shipping a fake detail.
function DocumentDetailStub({ route }: { route: { params: { id: string } } }) {
  return (
    <View style={styles.root}>
      <Text style={typeScale.title}>Document</Text>
      <Text style={[typeScale.muted, styles.note]}>detail lands in M1.4 (#773)</Text>
      <Text style={[typeScale.small, styles.note]}>id: {route.params.id}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  note: { marginTop: spacing.sm, textAlign: "center" },
});

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
        {({ route }) => <DocumentDetailStub route={route} />}
      </Stack.Screen>
    </Stack.Navigator>
  );
}
