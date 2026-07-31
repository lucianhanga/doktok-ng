import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";

import { ThreadsScreen } from "./ThreadsScreen";
import { ChatThreadScreen } from "./ChatThreadScreen";
import { colors } from "../theme";

// Chat stack (#776): thread list -> conversation. Citations jump cross-tab into the Documents
// stack's detail screen (nested-navigation params).
export type ChatStackParamList = {
  ThreadsList: undefined;
  ChatThread: { threadId: string | null; title?: string };
};

const Stack = createNativeStackNavigator<ChatStackParamList>();

export function ChatStack() {
  const navigation = useNavigation();

  function openDocument(documentId: string) {
    // Chat lives in its own tab stack; the document detail is in the Documents tab's stack.
    const navigate = navigation.getParent()?.navigate as
      | ((screen: string, params?: unknown) => void)
      | undefined;
    navigate?.("Documents", { screen: "DocumentDetail", params: { id: documentId } });
  }

  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTitleStyle: { color: colors.text },
        headerTintColor: colors.accent,
      }}
    >
      <Stack.Screen name="ThreadsList" options={{ title: "Chat", headerShown: false }}>
        {({ navigation: nav }) => (
          <ThreadsScreen
            onOpenThread={(threadId, title) => nav.navigate("ChatThread", { threadId, title })}
            onNewChat={() => nav.navigate("ChatThread", { threadId: null })}
          />
        )}
      </Stack.Screen>
      <Stack.Screen
        name="ChatThread"
        options={({ route }) => ({ title: route.params.title || "conversation" })}
      >
        {({ route, navigation: nav }) => (
          <ChatThreadScreen
            threadId={route.params.threadId}
            onOpenDocument={openDocument}
            onTitle={(title) => nav.setOptions({ title })}
          />
        )}
      </Stack.Screen>
    </Stack.Navigator>
  );
}
