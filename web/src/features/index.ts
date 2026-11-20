// Part 9: new web screens. Each export is a React component with no props.
import { createElement } from 'react';

const stub = (name: string) => {
  const C = () => createElement('main', { className: 'ft-stub' }, name);
  C.displayName = name;
  return C;
};

export const WelcomeScreen = stub('Welcome');
export const ConnectScreen = stub('Connect');
export const TasteScreen = stub('Taste');
export const WeekScreen = stub('Week');
export const AskScreen = stub('Ask');
export const ActivityScreen = stub('Activity');
export const OpsScreen = stub('Ops');
