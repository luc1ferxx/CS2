// One line for the library to show once, left by a page that sent the player
// there (a match deleted from its own page). Module memory, not the URL: the
// match's name never ends up in the address bar or the history.
let pending: string | null = null;

export function leaveLibraryNotice(message: string): void {
  pending = message;
}

export function takeLibraryNotice(): string | null {
  const message = pending;
  pending = null;
  return message;
}
