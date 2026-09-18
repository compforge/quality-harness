/** Plain values that can be persisted without serializing runtime objects. */
export type JsonValue = null | boolean | number | string | readonly JsonValue[] | JsonObject;
export type JsonObject = { readonly [key: string]: JsonValue };
