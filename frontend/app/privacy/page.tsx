import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { AppBrand } from "@/components/layout/AppBrand";
import { SiteFooter, VALVE_DISCLAIMER } from "@/components/layout/SiteFooter";
import { dataRegion, privacyContact } from "@/lib/privacy";

export const metadata: Metadata = {
  title: "隐私说明"
};

const PRIVACY_UPDATED = "2026-09-26";

// Public: visitors read it before signing in, so it never sits behind the auth boundary.
export default function PrivacyPage() {
  const region = dataRegion(process.env.NEXT_PUBLIC_DATA_REGION);
  const contact = privacyContact(process.env.NEXT_PUBLIC_PRIVACY_CONTACT);

  return (
    <main className="app-shell privacy-app">
      <header className="topbar">
        <AppBrand />
      </header>

      <div className="page">
        <article className="panel privacy-doc" aria-labelledby="privacy-title">
          <div className="panel-bar">
            <h1 className="panel-bar-title" id="privacy-title">隐私说明</h1>
          </div>

          <PrivacySection id="what" title="这是什么">
            <p>
              一个邀请制的 CS2 比赛复盘网站：你上传比赛录像（.dem），网站解析出 2D 战术回放，并按固定规则给出复盘建议。建议由规则计算，不使用 AI 大模型；网站不投放广告，也不做访问统计分析。
            </p>
          </PrivacySection>

          <PrivacySection id="sign-in" title="登录时我们拿到什么">
            <p>
              用 Steam 登录时，Steam 只返回你的 SteamID64（一串 17 位数字），我们不会拿到你的 Steam 密码。之后服务器用 Steam Web API 读取你的公开昵称和头像地址并保存，每次登录时更新。
            </p>
            <p>账户里还会记下创建时间和最近一次登录的时间。未受邀的账号不会被保存，但登录那一次仍会向 Steam 查询一次它的公开资料。</p>
            <p>
              邀请名单（受邀者的 SteamID64）由站长保存在服务器配置里。删除账户不会把你移出名单；如需移出，请联系站长。
            </p>
          </PrivacySection>

          <PrivacySection id="matches" title="你上传的比赛">
            <ul>
              <li>原始 .dem 文件按原样保存，用于以后重新解析。</li>
              <li>解析出的回放数据和复盘建议。</li>
              <li>你对建议的评价（有帮助、无关、判断不足），以及你改过的比赛名。</li>
            </ul>
            <p>
              <strong>.dem 文件里包含同场所有玩家的 SteamID64、游戏内昵称、位置和击杀记录。</strong>
              这些内容只有上传者本人能看到。同场的其他玩家如果希望移除，可以按下方「联系」里的方式提出，站长会代为删除这场比赛。
            </p>
          </PrivacySection>

          <PrivacySection id="steam-history" title="Steam 比赛记录（可选）">
            <p>
              只在你主动关联时使用：游戏验证码和比赛分享码加密保存，只有你每次点击“同步”时才向 Valve 查询你的比赛记录。断开关联即删除这些信息。
            </p>
          </PrivacySection>

          <PrivacySection id="cookies" title="Cookie 和浏览器存储">
            <p>只有两个必需的 Cookie，没有统计或广告 Cookie：</p>
            <ul>
              <li><code>__Host-cs2_session</code>：保持登录，1 小时后过期。</li>
              <li><code>__Host-cs2_steam_state</code>：只在登录过程中使用，5 分钟后过期。</li>
            </ul>
            <p>
              浏览器本地存储里有一项“你在比赛里选的玩家”偏好（含你的 SteamID64）。删除账户时会在当前浏览器清除，你也可以随时在浏览器设置里清除本站数据。
            </p>
          </PrivacySection>

          <PrivacySection id="logs" title="日志">
            <p>
              服务器访问日志记录 IP 地址、浏览器标识和访问的页面地址（包括搜索词），用于排查故障和防止滥用。日志按大小轮转（每个服务最多约 50 MB），不按时间删除。
            </p>
            <p>登录请求按 IP 地址限速：限速记录只保存 IP 地址的哈希值，约 1 分钟后自动过期。</p>
          </PrivacySection>

          <PrivacySection id="where" title="数据存在哪里、谁能接触">
            <p>网站服务器（VPS）：{region}。比赛文件和备份保存在 Cloudflare R2 的私有存储桶里。只有站长能访问服务器。</p>
            <p>经手数据的第三方：</p>
            <ul>
              <li>VPS 服务商：运行网站服务器。</li>
              <li>Cloudflare：存储比赛文件和备份。</li>
              <li>Valve / Steam：登录、读取公开资料，以及你主动开启的比赛记录同步。</li>
              <li>Let’s Encrypt：只签发 HTTPS 证书，不涉及你的数据。</li>
            </ul>
            <p>我们不出售你的数据，也不共享给其他人。</p>
          </PrivacySection>

          <PrivacySection id="retention" title="保存多久">
            <p>账户和比赛会一直保存，直到你删除，或站长应你的要求代为删除；登录会话 1 小时后过期。</p>
            <p>被移出邀请名单后你将无法再登录，但数据不会因此自动删除；需要删除时请联系站长代为处理。</p>
          </PrivacySection>

          <PrivacySection id="deletion" title="删除">
            <ul>
              <li>在<Link href="/dashboard">我的比赛</Link>里可以永久删除单场比赛。</li>
              <li>在<Link href="/account">账户与数据</Link>页可以删除账户及全部数据，所有设备上的登录同时失效。</li>
              <li>无法登录时（例如已被移出邀请名单），可以联系站长代为删除比赛或账户。</li>
            </ul>
            <p>
              删除会立即从网站的数据库和存储中移除（存储清理失败时会自动重试）。每日备份里的副本最多再保留 30 天，之后自动清除；如果站长从备份恢复了数据库，会重新执行在那之后的删除。
            </p>
            <p>删除比赛不会恢复当天的上传次数。</p>
          </PrivacySection>

          <PrivacySection id="contact" title="联系">
            {contact === null ? (
              <p>本站没有公开联系方式。受邀用户请直接联系邀请你的站长；同场的其他玩家可以请上传这场比赛的人删除它。</p>
            ) : contact.kind === "link" ? (
              <p>
                隐私问题或删除请求：
                <a href={contact.href} {...(contact.external ? { target: "_blank", rel: "noreferrer" } : {})}>
                  {contact.label}
                </a>
              </p>
            ) : (
              <p>隐私问题或删除请求：{contact.label}</p>
            )}
          </PrivacySection>

          <PrivacySection id="valve" title="与 Valve 无关联">
            <p>{VALVE_DISCLAIMER}</p>
          </PrivacySection>

          <p className="privacy-updated">最近更新：{PRIVACY_UPDATED}</p>
        </article>
      </div>
      <SiteFooter current="privacy" />
    </main>
  );
}

function PrivacySection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  const headingId = `privacy-${id}`;
  return (
    <section className="privacy-section" aria-labelledby={headingId}>
      <h2 className="privacy-section-bar" id={headingId}>{title}</h2>
      <div className="privacy-section-body">{children}</div>
    </section>
  );
}
