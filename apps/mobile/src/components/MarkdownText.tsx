import React from "react";
import { StyleSheet, Text } from "react-native";
import Markdown from "react-native-markdown-display";

import { colors } from "../theme";

// Shared markdown renderer for long-form text (chat answers #776). Same idiom as the document
// detail's content tab: every text node is selectable so users can copy text out.
const MD_RULES = {
  text: (
    node: { key: string; content: string },
    _children: unknown,
    _parent: unknown,
    styles: Record<string, unknown>,
    inheritedStyles: Record<string, unknown> = {},
  ) => (
    <Text key={node.key} style={[inheritedStyles, styles.text] as never} selectable>
      {node.content}
    </Text>
  ),
};

const mdStyles = StyleSheet.create({
  body: { color: colors.text, fontSize: 14, lineHeight: 21 },
  heading3: { color: colors.text, fontSize: 15, fontWeight: "600", marginVertical: 6 },
  code_inline: {
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderRadius: 4,
    paddingHorizontal: 4,
    fontFamily: "Menlo",
    fontSize: 13,
  },
  fence: {
    color: colors.text,
    backgroundColor: colors.surfaceAlt,
    borderRadius: 6,
    padding: 8,
    fontFamily: "Menlo",
    fontSize: 13,
    borderWidth: 0,
  },
});

export function MarkdownText({ children }: { children: string }) {
  return (
    <Markdown
      style={{
        body: mdStyles.body,
        heading3: mdStyles.heading3,
        code_inline: mdStyles.code_inline,
        fence: mdStyles.fence,
      }}
      rules={MD_RULES}
    >
      {children}
    </Markdown>
  );
}
