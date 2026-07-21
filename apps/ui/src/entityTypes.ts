/** Shared display metadata for entity types (mirrors the EntityType enum in
 * doktok_contracts/schemas.py): friendly label, chip color, and a one-letter badge.
 * Used by the Knowledge Graph panel and the document Entities subtab (#538). */

export interface EntityTypeMeta {
  label: string;
  color: string;
  badge: string;
}

export const ENTITY_TYPE_META: Record<string, EntityTypeMeta> = {
  PERSON: { label: "Person", color: "#1d6fa8", badge: "P" },
  ORG: { label: "Organization", color: "#7c3aed", badge: "O" },
  GPE: { label: "Place", color: "#0d7d7d", badge: "G" },
  LOCATION: { label: "Location", color: "#0f766e", badge: "L" },
  EMAIL: { label: "Email", color: "#c2410c", badge: "E" },
  URL: { label: "Link", color: "#9333ea", badge: "U" },
  CUSTOM_TOKEN: { label: "Token", color: "#64748b", badge: "C" },
  // Validated structured identifiers (#518 Phase 1).
  PHONE: { label: "Phone", color: "#b45309", badge: "T" },
  ADDRESS: { label: "Address", color: "#57534e", badge: "A" },
  POSTAL_CODE: { label: "Postal code", color: "#0891b2", badge: "Z" },
  IBAN: { label: "IBAN", color: "#0e7490", badge: "B" },
  VAT_ID: { label: "VAT ID", color: "#9a3412", badge: "V" },
  TAX_NUMBER: { label: "Tax number", color: "#92400e", badge: "X" },
  REGISTRATION_NUMBER: { label: "Registration number", color: "#4d7c0f", badge: "R" },
  // Semantic type (#518 Phase 2).
  JOB_TITLE: { label: "Job title", color: "#be185d", badge: "J" },
  // Historical / id types.
  DATE: { label: "Date", color: "#a16207", badge: "D" },
  MONEY: { label: "Money", color: "#16a34a", badge: "$" },
  DOCUMENT_ID: { label: "Document ID", color: "#475569", badge: "I" },
  INVOICE_ID: { label: "Invoice ID", color: "#374151", badge: "N" },
  CONTRACT_ID: { label: "Contract ID", color: "#1e293b", badge: "K" },
};

const OTHER: EntityTypeMeta = { label: "", color: "#555e6d", badge: "?" };

/** Display metadata for an entity type; unknown types fall back to the RAW type as the label
 * (a new backend type is always surfaced, never hidden behind a generic "Other"). */
export function entityTypeMeta(entityType: string): EntityTypeMeta {
  return ENTITY_TYPE_META[entityType] ?? { ...OTHER, label: entityType };
}
