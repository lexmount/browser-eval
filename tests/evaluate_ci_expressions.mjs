// Evaluate the committed concurrency policy with GitHub's expression implementation.
import fs from 'node:fs';
import {Lexer, Parser, Evaluator, data} from '@actions/expressions';

const {policy, contexts, partitions} = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = contexts.map(github => {
  const context = JSON.parse(JSON.stringify({github,
    needs: {scope: {outputs: {docs_only: github.docs_only ?? ''}}}}), data.reviver);
  const evaluate = expression => {
    const tokens = new Lexer(expression.trim()).lex().tokens;
    const tree = new Parser(tokens, ['github', 'needs'], []).parse();
    return new Evaluator(tree, context).evaluate().coerceString();
  };
  const render = template => template.replace(/\$\{\{(.*?)\}\}/g, (_, expression) => evaluate(expression));
  const result = {group: render(policy.group), cancel: render(policy['cancel-in-progress']) === 'true'};
  if (partitions) {
    const expression = partitions.replace(/^\$\{\{\s*|\s*\}\}$/g, '');
    result.partitions = JSON.parse(evaluate(`toJSON(${expression})`));
  }
  return result;
});
console.log(JSON.stringify(results));
