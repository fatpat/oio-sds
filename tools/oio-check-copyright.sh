#!/usr/bin/env bash
set -e

BASEDIR=$1
[[ -n "$BASEDIR" ]]
[[ -d "$BASEDIR" ]]

/bin/ls -1f ${BASEDIR} \
| grep -i -v -e '^\.' -e '^build' -e '^cmake' -e '^setup' \
| while read D ; do
	/usr/bin/find "${BASEDIR}/${D}" -type f \
		-name '*.h' -or -name '*.c' -or -name '*.py' -or -name '*.go' \
	| while read F ; do
		if ! [[ -s "$F" ]] ; then continue ; fi
		if ! /usr/bin/git ls-files --error-unmatch "$F" ; then continue ; fi
		if ! /bin/grep -q 'Copyright' "$F" ; then
			echo "Missing Copyright section in $F" 1>&2
			exit 1
		fi
	done
done

if [ -n "$TRAVIS_COMMIT_RANGE" ]
then
	INCLUDE='.+\.(c|go|h|py)$'
	YEAR=$(date +%Y)
	FAIL=0
	FILES=$(git diff --name-only "$TRAVIS_COMMIT_RANGE" | grep -E "$INCLUDE")
	echo "Checking copyright for year $YEAR."
	for name in $FILES
	do
		COPYRIGHT_LINE=$(grep -E 'Copyright.+[[:digit:]]{4}.+OpenIO' "$name")
		if [[ ! "$COPYRIGHT_LINE" =~ .+$YEAR.* ]]
		then
			echo "File $name has just been modified ($YEAR),"
			echo "but copyright is '$COPYRIGHT_LINE'."
			FAIL=1
		fi
	done
	exit $FAIL
	echo "OK"
fi
