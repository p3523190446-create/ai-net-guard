// Clash Verge Rev 全局扩展脚本（AI NetGuard 专用防断连分流方案）
// 使用方法：Clash Verge Rev -> 订阅 -> 右键你的订阅 -> 编辑扩展脚本（或全局扩展脚本）-> 粘贴保存并启用

function main(config) {
  const proxies = Array.isArray(config.proxies) ? config.proxies : [];

  const INFO_REGEX = /剩余|流量|过期|到期|官网|网址|群|通知|维护|重置|套餐|距离|客服|返利|购买|试用|DIRECT|REJECT/i;
  const RESTRICTED_REGEX = /香港|港|HK|Hong\s*Kong|中国|CN|澳门|MO|Macau|俄|RU|Russia/i;
  const PREFERRED_REGEX = /日本|东京|大阪|JP|Japan|新加坡|狮城|SG|Singapore|台湾|台北|TW|Taiwan|美国|US|America|洛杉矶|圣何塞|硅谷|韩国|KR|Korea|德国|DE|英国|UK/i;

  const allUsableNames = proxies
    .map((p) => p && p.name)
    .filter((name) => typeof name === "string" && name.trim().length > 0 && !INFO_REGEX.test(name));

  let aiNodes = allUsableNames.filter((name) => !RESTRICTED_REGEX.test(name));
  // 优先排序日/新/台/美/韩/欧节点，避免把不稳定冷门地区排在前面
  aiNodes.sort((a, b) => {
    const aPref = PREFERRED_REGEX.test(a) ? 0 : 1;
    const bPref = PREFERRED_REGEX.test(b) ? 0 : 1;
    return aPref - bPref;
  });

  if (aiNodes.length === 0) {
    aiNodes = allUsableNames.slice();
  }
  if (aiNodes.length === 0) {
    aiNodes = ["DIRECT"];
  }

  const dailyNodes = allUsableNames.length > 0 ? allUsableNames.slice() : ["DIRECT"];

  // 确保存在固定顺序故障转移池（fallback）：正常时不乱跳 IP，挂了自动秒切下一个
  const aiFallbackGroup = {
    name: "🤖 AI-稳定池",
    type: "fallback",
    proxies: aiNodes,
    url: "https://www.google.com/generate_204",
    interval: 120,
    timeout: 4000,
    lazy: false,
  };

  const dailyUrlTestGroup = {
    name: "🌍 海外日常",
    type: "url-test",
    proxies: dailyNodes,
    url: "https://www.gstatic.com/generate_204",
    interval: 300,
    tolerance: 80,
    lazy: true,
  };

  const manualSelectGroup = {
    name: "🎯 手动挑选",
    type: "select",
    proxies: ["🤖 AI-稳定池", "🌍 海外日常", ...dailyNodes],
  };

  const existingGroups = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];
  const customNames = new Set([aiFallbackGroup.name, dailyUrlTestGroup.name, manualSelectGroup.name]);
  const filteredGroups = existingGroups.filter((g) => g && !customNames.has(g.name));

  config["proxy-groups"] = [aiFallbackGroup, dailyUrlTestGroup, manualSelectGroup, ...filteredGroups];

  // 置顶强分流规则：国内 AI/高频国内服务强制直连；国外 AI 与代码服务固定走 🤖 AI-稳定池
  const directDomains = [
    "DOMAIN-SUFFIX,deepseek.com,DIRECT",
    "DOMAIN-SUFFIX,deepseek.ai,DIRECT",
    "DOMAIN-SUFFIX,moonshot.cn,DIRECT",
    "DOMAIN-SUFFIX,kimi.ai,DIRECT",
    "DOMAIN-SUFFIX,doubao.com,DIRECT",
    "DOMAIN-SUFFIX,volces.com,DIRECT",
    "DOMAIN-SUFFIX,qishui.com,DIRECT",
    "DOMAIN-SUFFIX,douyin.com,DIRECT",
    "DOMAIN-SUFFIX,douyinvod.com,DIRECT",
    "DOMAIN-SUFFIX,douyinpic.com,DIRECT",
    "DOMAIN-SUFFIX,douyinstatic.com,DIRECT",
    "DOMAIN-SUFFIX,snssdk.com,DIRECT",
    "DOMAIN-SUFFIX,ctobsnssdk.com,DIRECT",
    "DOMAIN-SUFFIX,zijieapi.com,DIRECT",
    "DOMAIN-SUFFIX,pstatp.com,DIRECT",
    "DOMAIN-SUFFIX,ibytedtos.com,DIRECT",
    "DOMAIN-SUFFIX,byteimg.com,DIRECT",
    "DOMAIN-SUFFIX,bytegoofy.com,DIRECT",
    "DOMAIN-SUFFIX,bytescm.com,DIRECT",
    "DOMAIN-SUFFIX,bytednsdoc.com,DIRECT",
    "DOMAIN-SUFFIX,zjcdn.com,DIRECT",
    "DOMAIN-SUFFIX,amemv.com,DIRECT",
    "DOMAIN-SUFFIX,bytecdn.cn,DIRECT",
    "DOMAIN-SUFFIX,qwen.ai,DIRECT",
    "DOMAIN-SUFFIX,dashscope.aliyuncs.com,DIRECT",
    "DOMAIN-SUFFIX,aliyuncs.com,DIRECT",
    "DOMAIN-SUFFIX,bigmodel.cn,DIRECT",
    "DOMAIN-SUFFIX,qq.com,DIRECT",
    "DOMAIN-SUFFIX,weixin.qq.com,DIRECT",
    "DOMAIN-SUFFIX,edu.cn,DIRECT",
    "GEOIP,LAN,DIRECT,no-resolve",
  ];

  const aiProxyDomains = [
    "DOMAIN-SUFFIX,antigravity.google,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,generativelanguage.googleapis.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,googleapis.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,google.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,gstatic.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,gemini.google.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,aistudio.google.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,openai.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,chatgpt.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,oaistatic.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,oaiusercontent.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,anthropic.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,claude.ai,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,cursor.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,cursor.sh,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,github.com,🤖 AI-稳定池",
    "DOMAIN-SUFFIX,githubusercontent.com,🤖 AI-稳定池",
  ];

  const existingRules = Array.isArray(config.rules) ? config.rules.slice() : [];
  const hasGeoCn = existingRules.some((r) => typeof r === "string" && /^GEOIP\s*,\s*CN\s*,/i.test(r));

  let matchIndex = existingRules.findIndex((r) => typeof r === "string" && /^MATCH\s*,/i.test(r));
  if (!hasGeoCn) {
    if (matchIndex >= 0) {
      existingRules.splice(matchIndex, 0, "GEOIP,CN,DIRECT");
    } else {
      existingRules.push("GEOIP,CN,DIRECT");
    }
  }
  if (!existingRules.some((r) => typeof r === "string" && /^MATCH\s*,/i.test(r))) {
    existingRules.push("MATCH,🌍 海外日常");
  }

  config.rules = [...directDomains, ...aiProxyDomains, ...existingRules];
  return config;
}
