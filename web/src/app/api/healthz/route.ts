/**
 * Liveness probe for the web container.
 *
 * Deliberately checks nothing but this process. If it also probed the API, a
 * brief gateway blip would make Docker restart a perfectly healthy frontend,
 * turning a small outage into a larger one.
 */

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok", service: "web" });
}
