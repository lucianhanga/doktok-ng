import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { BACKEND_URL } from "../config";
import { colors, spacing, typeScale } from "../theme";

// Login screen (#771): tenant + email + password against /api/v1/auth/login, spartan like the
// web login. Shows the backend URL so a wrong target is obvious on a physical device.
export function LoginScreen() {
  const { signIn } = useAuth();
  const [tenantId, setTenantId] = useState("dev");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await signIn(tenantId, email, password);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <Text style={[typeScale.title, styles.appName]}>DokTok NG</Text>
      <Text style={[typeScale.muted, styles.backend]}>backend: {BACKEND_URL}</Text>

      <TextInput
        style={styles.input}
        placeholder="tenant id"
        placeholderTextColor={colors.muted}
        autoCapitalize="none"
        value={tenantId}
        onChangeText={setTenantId}
      />
      <TextInput
        style={styles.input}
        placeholder="email"
        placeholderTextColor={colors.muted}
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        style={styles.input}
        placeholder="password"
        placeholderTextColor={colors.muted}
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        onSubmitEditing={submit}
      />

      {error && (
        <Text style={styles.error} role="alert">
          {error}
        </Text>
      )}

      <TouchableOpacity
        style={[styles.button, busy && styles.buttonBusy]}
        onPress={submit}
        disabled={busy}
        accessibilityRole="button"
      >
        {busy ? (
          <ActivityIndicator color={colors.bg} />
        ) : (
          <Text style={styles.buttonText}>Sign in</Text>
        )}
      </TouchableOpacity>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
    justifyContent: "center",
    padding: spacing.xl,
  },
  appName: { textAlign: "center", marginBottom: spacing.sm },
  backend: { textAlign: "center", marginBottom: spacing.xl },
  input: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 8,
    color: colors.text,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    marginBottom: spacing.md,
    fontSize: 15,
  },
  error: { color: colors.danger, marginBottom: spacing.md, textAlign: "center" },
  button: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.sm,
  },
  buttonBusy: { opacity: 0.6 },
  buttonText: { color: colors.bg, fontWeight: "700", fontSize: 15 },
});
