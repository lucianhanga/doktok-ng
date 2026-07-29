import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StyleSheet, Text, View } from "react-native";

import { DocumentsScreen } from "./DocumentsScreen";
import { DocumentDetailScreen } from "./DocumentDetailScreen";
import { PdfViewerScreen } from "./PdfViewerScreen";
import { colors } from "../theme";

export type DocumentsStackParamList = {
  DocumentsList: undefined;
  DocumentDetail: { id: string };
  PdfViewer: { id: string; variant: "original" | "normalized"; title?: string };
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
        {({ route, navigation }) => (
          <DocumentDetailScreen
            id={route.params.id}
            onOpenPdf={(variant) =>
              navigation.navigate("PdfViewer", {
                id: route.params.id,
                variant,
                title: undefined,
              })
            }
          />
        )}
      </Stack.Screen>
      <Stack.Screen name="PdfViewer" options={{ title: "PDF" }}>
        {({ route }) => (
          <PdfViewerScreen
            id={route.params.id}
            variant={route.params.variant}
            title={route.params.title}
          />
        )}
      </Stack.Screen>
    </Stack.Navigator>
  );
}
