"use client";

import { useRouter } from "next/navigation";
import { AuthForm } from "@/components/auth/AuthForm";
import { useAuth } from "@/context/AuthProvider";

export default function RegisterPage() {
  const { register } = useAuth();
  const router = useRouter();

  return (
    <div className="space-y-6 max-w-lg">
      <h1 className="text-2xl font-semibold">Create account</h1>
      <AuthForm
        mode="register"
        onSubmit={async ({ email, password, displayName }) => {
          await register(email, password, displayName);
          router.push("/account");
        }}
      />
    </div>
  );
}
