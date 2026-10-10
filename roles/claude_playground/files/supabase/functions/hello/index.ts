// Example playground edge function — served at <SUPABASE_URL>/functions/v1/hello
// (or same-origin: <pgN origin>/api/functions/v1/hello). Deploy = drop a
// <name>/index.ts next to this dir; no `supabase functions deploy` (that CLI
// command only targets the hosted cloud). See /workspace/supabase/README.md.
Deno.serve(async (req: Request) => {
  const { name } = await req.json().catch(() => ({ name: "playground" }))
  return new Response(
    JSON.stringify({ msg: `hello ${name}`, from: "playground edge function" }),
    { headers: { "Content-Type": "application/json" } },
  )
})
