"""Run this project's full unittest suite and the identity handoff checks."""
import subprocess
import sys
import unittest


def main():
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    if not successful:
        return 1
    # V2 merge sealing, identity issuance and offline lookup checks.
    identity = subprocess.run([sys.executable, '-B', 'identity_examples.py'])
    if identity.returncode != 0:
        print('IDENTITY CHECK result=FAIL')
        return identity.returncode
    print('IDENTITY CHECK result=PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
