import logoLight from "@/assets/logo-light.svg";
import logoDark from "@/assets/logo-dark.svg";

export function LoadingScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-[var(--bg-canvas)]">
      <img
        src={logoLight}
        alt="DuckHaven"
        className="w-44 h-auto block dark:hidden"
      />
      <img
        src={logoDark}
        alt="DuckHaven"
        className="w-44 h-auto hidden dark:block"
      />
    </div>
  );
}
