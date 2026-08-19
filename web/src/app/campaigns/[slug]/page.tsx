/**
 * Route shell for a campaign's detail page.
 *
 * A server component purely so `generateStaticParams` can live here: `output: "export"` needs to
 * know every dynamic segment ahead of time, and the slugs come from the committed snapshot. The
 * page itself is the client component below.
 */

import seed from "@/lib/seed.json";
import CampaignDetail from "./CampaignDetail";

export function generateStaticParams() {
  const slugs = Object.keys((seed as { campaign_detail?: Record<string, unknown> }).campaign_detail ?? {});
  return slugs.map((slug) => ({ slug }));
}

export default async function CampaignDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <CampaignDetail slug={slug} />;
}
