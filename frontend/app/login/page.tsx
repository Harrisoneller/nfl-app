"use client";

import { useRouter } from "next/navigation";
import { AuthForm } from "@/components/auth/AuthForm";
import { useAuth } from "@/context/AuthProvider";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();

  return (
    <div className="space-y-6 max-w-lg">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <AuthForm
        mode="login"
        onSubmit={async ({ email, password }) => {
          await login(email, password);
          router.push("/account");
        }}
      />
    </div>
  );
}
