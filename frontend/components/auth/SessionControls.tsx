"use client";

import { LogOut } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth/AuthProvider";

export function SessionControls() {
  const { signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  return (
    <button
      className="secondary-button compact-button"
      type="button"
      disabled={signingOut}
      onClick={async () => {
        setSigningOut(true);
        await signOut();
        setSigningOut(false);
      }}
    >
      <LogOut size={14} />
      {signingOut ? "Signing out" : "Sign out"}
    </button>
  );
}
