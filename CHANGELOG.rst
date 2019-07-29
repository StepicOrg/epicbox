Changelog
=========

1.1.0 (2019-07-29)
------------------

Changes
^^^^^^^

* Added ``processes`` limits option to limit the number of processes that a sandbox can run.
  For example, it can be used to prevent a fork bomb or do not allow the sandboxed code to create
  new processes. It's unlimited by default.
  `#9 <https://github.com/StepicOrg/epicbox/pull/9>`_

1.0.0 (2018-11-10)
------------------

Breaking changes
^^^^^^^^^^^^^^^^

* Migrated from the ``docker-py`` Docker library  to ``docker`` version ``>=2``.
* ``epicbox.create``, ``epicbox.start``, ``epicbox.destroy`` now return and accept a ``Sandbox``
  object instead of a low-level container ``dict`` structure.
* Removed the obsolete ``base_workdir`` argument from ``epicbox.configure``.

Changes
^^^^^^^

* Unpinned and bumped versions of dependency packages in ``pyproject.toml`` and ``requirements.txt``.


0.6.2 (2018-11-05)
------------------

* Initial release on PyPI. (`#5 <https://github.com/StepicOrg/epicbox/issues/5>`_)
* Fix docker volume cleanup if an exception is raised during its usage.
