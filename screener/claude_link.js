const CLAUDE_LINK={"salt": "MessWyWd23G+sAF+uYfRkg==", "iv": "wAPZwKIaKPtGIXfQ", "ct": "Y+HF3+3s2KX7pc5r0CL7JWVQHTMAVJbRaKW9SFQ2oehg9LjC/us6R9LwgYzTmsrfwUVAU8EdijxlEIi+YCEtmxA=", "iter": 200000};
// 點「Claude 研判＋虛擬帳戶」：輸入密碼 → 瀏覽器端解密網址 → 開新分頁（網址不以明文出現在公開網站上）
async function _claudeUrl(pw){
  const d=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)), L=CLAUDE_LINK;
  const base=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:d(L.salt),iterations:L.iter,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['decrypt']);
  return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:d(L.iv)},key,d(L.ct)));
}
function openClaude(){
  if(!CLAUDE_LINK){alert('連結還沒設定');return false;}
  const pw=prompt('請輸入密碼');
  if(!pw)return false;
  const w=window.open('about:blank','_blank');  // 先開分頁（在點擊當下開，才不會被擋）
  _claudeUrl(pw).then(u=>{if(w)w.location=u;else location.href=u;})
    .catch(()=>{if(w)w.close();alert('密碼錯誤');});
  return false;
}
