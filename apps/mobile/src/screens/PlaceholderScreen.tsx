import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { colors, spacing, typeScale } from "../theme";

// Placeholder screen used by every tab in M1.1; real screens land per ticket (M1.3+).
export function PlaceholderScreen({ name, note }: { name: string; note: string }) {
  return (
    <View style={styles.root}>
      <Text style={typeScale.title}>{name}</Text>
      <Text style={[typeScale.muted, styles.note]}>{note}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
  },
  note: {
    marginTop: spacing.sm,
    textAlign: "center",
  },
});
