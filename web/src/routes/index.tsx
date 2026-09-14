import { createFileRoute } from "@tanstack/react-router"
import { Button } from "@/components/ui/button"

export const Route = createFileRoute("/")({ component: App })

function App() {
  return (
    <main className="flex min-h-svh flex-col bg-background text-foreground">
      <div
        role="status"
        className="border-b border-border bg-muted px-4 py-2 text-center text-xs font-semibold tracking-widest"
      >
        SIMULATED — NO REAL ORDERS
      </div>
      <section className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-4 p-6">
        <p className="text-xs font-semibold tracking-widest text-muted-foreground uppercase">
          Foundation only
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Sopara</h1>
        <p className="max-w-xl text-sm leading-6 text-muted-foreground">
          The interface foundation is installed. Application workflows begin in
          a later authorized work package.
        </p>
        <Button disabled className="w-fit">
          No commands available
        </Button>
      </section>
    </main>
  )
}
