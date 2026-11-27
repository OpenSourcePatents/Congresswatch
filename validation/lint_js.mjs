import fs from 'node:fs';
import { ESLint } from 'eslint';
import globals from 'globals';
const scripts=[...fs.readFileSync('index.html','utf8').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)];
const lint=new ESLint({overrideConfigFile:true,overrideConfig:[{
 languageOptions:{ecmaVersion:2022,sourceType:'script',globals:{...globals.browser,va:'readonly'}},
 rules:{'no-undef':'error','no-unreachable':'error','no-dupe-args':'error','no-dupe-keys':'error',
 'no-func-assign':'error','no-unexpected-multiline':'error','valid-typeof':'error','constructor-super':'error'}
}]});
let errors=0;
for(let i=0;i<scripts.length;i++){
 const results=await lint.lintText(scripts[i][1],{filePath:'inline-'+i+'.js'});
 errors+=results.reduce((n,r)=>n+r.errorCount,0);
 if(results.some(r=>r.messages.length))console.log(await (await lint.loadFormatter('stylish')).format(results));
}
console.log(JSON.stringify({inline_scripts:scripts.length,lint_errors:errors}));
process.exitCode=errors?1:0;
