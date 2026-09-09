const SCRIPT_SUFFIXES = new Set([
  ".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".pl",
]);
const OPERATORS = new Set(["&&", "||", ";", "|"]);

function basename(value: string): string {
  return value.split(/[\\/]/).pop() ?? value;
}

function shellTokens(command: string): string[] | undefined {
  const tokens: string[] = [];
  let token = "";
  let quote: "'" | "\"" | undefined;
  const finish = () => {
    if (token) tokens.push(token);
    token = "";
  };
  for (let index = 0; index < command.length; index += 1) {
    const character = command[index]!;
    if (quote) {
      if (character === quote) quote = undefined;
      else if (character === "\\" && quote === "\"") {
        index += 1;
        if (index < command.length) token += command[index];
      } else token += character;
      continue;
    }
    if (character === "'" || character === "\"") {
      quote = character;
      continue;
    }
    if (character === "\\") {
      index += 1;
      if (index < command.length) token += command[index];
      continue;
    }
    if (/\s/.test(character)) {
      finish();
      continue;
    }
    const pair = command.slice(index, index + 2);
    if (pair === "&&" || pair === "||") {
      finish();
      tokens.push(pair);
      index += 1;
      continue;
    }
    if (character === ";" || character === "|") {
      finish();
      tokens.push(character);
      continue;
    }
    token += character;
  }
  if (quote) return undefined;
  finish();
  return tokens;
}

function commandName(command: unknown): string {
  if (typeof command !== "string" || !command.trim()) return "";
  const tokens = shellTokens(command) ?? command.trim().split(/\s+/);
  for (const token of tokens) {
    const candidate = token.replace(/[;&|]+$/, "");
    const dot = candidate.lastIndexOf(".");
    if (dot >= 0 && SCRIPT_SUFFIXES.has(candidate.slice(dot).toLowerCase())) {
      return basename(candidate);
    }
  }
  const segments: string[][] = [[]];
  for (const token of tokens) {
    if (OPERATORS.has(token)) segments.push([]);
    else segments.at(-1)!.push(token);
  }
  for (const segment of segments.reverse()) {
    const executable = segment.find((token) => (
      !token.startsWith("-") && !token.startsWith(">") && !token.startsWith("<")
      && !token.includes("=")
    ));
    if (executable && executable !== "cd") return basename(executable);
  }
  return "";
}

export function toolNameDetail(value: unknown): string {
  let argumentsValue = value;
  if (typeof argumentsValue === "string") {
    try {
      argumentsValue = JSON.parse(argumentsValue) as unknown;
    } catch {
      return "";
    }
  }
  if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    return "";
  }
  const argumentsRecord = argumentsValue as Record<string, unknown>;
  const command = commandName(argumentsRecord.command);
  if (command) return command;
  for (const key of ["file_path", "path", "filename", "file"]) {
    const candidate = argumentsRecord[key];
    if (typeof candidate === "string" && candidate) return basename(candidate);
  }
  return "";
}
