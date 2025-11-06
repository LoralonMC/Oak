# Contributing to Oak

Thank you for your interest in contributing to Oak! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/yourusername/oak.git`
3. Create a new branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Test your changes thoroughly
6. Commit with clear messages: `git commit -m "Add feature: description"`
7. Push to your fork: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Code Standards

### Python Style
- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Add docstrings to all functions and classes
- Keep functions focused and single-purpose

### Branch Development
When creating new branches:
- Use the `create_branch.py` script as a starting point
- Follow the existing branch structure
- Keep branches self-contained
- Include a `config.yml` with all configurable settings
- Document all configuration options

### Configuration
- Never hardcode IDs or settings in code
- All configurable values should be in `config.yml`
- Provide sensible defaults in `DEFAULT_CONFIG`
- Document what each setting does

### Database
- Use per-branch databases (`data.db` in branch folder)
- Use parameterized queries (prevent SQL injection)
- Include database schema in branch file
- Handle database errors gracefully

### Error Handling
- Use try-except blocks for operations that can fail
- Log errors with appropriate severity levels
- Provide user-friendly error messages
- Never expose sensitive information in errors

## Testing

Before submitting a pull request:

1. **Syntax Check**: Run `python -m py_compile` on your files
2. **Test in Development**: Use a test Discord server
3. **Test Hot-Reload**: Verify `!reload` works with your branch
4. **Test Configuration**: Ensure config changes work correctly
5. **Check Logs**: Review logs for warnings or errors

## Branch Guidelines

### Branch Structure
```
branches/your_branch/
├── __init__.py       # Package setup with async def setup(bot)
├── branch.py         # Main branch code
├── config.yml        # Configuration file
└── data.db           # Database (if needed, auto-created)
```

### Required Files

**__init__.py**
```python
"""
BranchName
Description
"""

from .branch import BranchName

async def setup(bot):
    await bot.add_cog(BranchName(bot))
```

**config.yml**
```yaml
enabled: true
version: "1.0.0"
settings:
  # Your settings here
```

### Naming Conventions
- Branch folders: `snake_case` (e.g., `my_feature`)
- Class names: `PascalCase` (e.g., `MyFeature`)
- Functions/methods: `snake_case` (e.g., `handle_event`)
- Commands: `snake_case` (e.g., `!my_command`)

## Pull Request Guidelines

### PR Title Format
- `Feature: Add XYZ branch`
- `Fix: Resolve issue in XYZ`
- `Docs: Update XYZ documentation`
- `Refactor: Improve XYZ code`

### PR Description
Include:
1. **What**: What changes does this PR make?
2. **Why**: Why are these changes needed?
3. **How**: How were the changes implemented?
4. **Testing**: What testing was performed?
5. **Screenshots**: If UI changes, include screenshots

### Checklist
- [ ] Code follows project style guidelines
- [ ] All files pass syntax check
- [ ] Tested in development environment
- [ ] Configuration is properly documented
- [ ] No hardcoded values
- [ ] Error handling is implemented
- [ ] Logging is appropriate
- [ ] README updated (if needed)
- [ ] No sensitive information in code

## What to Contribute

### Welcome Contributions
- **New Branches**: Useful, generic features
- **Bug Fixes**: Fix issues in existing code
- **Documentation**: Improve READMEs, docstrings, comments
- **Examples**: Add example branches or configs
- **Tests**: Add testing infrastructure
- **Performance**: Optimize existing code

### Not Accepted
- Server-specific features (should be configurable)
- Breaking changes without discussion
- Code that doesn't follow standards
- Features without proper documentation
- Malicious or harmful code

## Branch Ideas

Looking for contribution ideas? Consider:
- Moderation tools (warnings, kicks, bans)
- Welcome/goodbye messages
- Reaction roles
- Poll/voting systems
- Leveling/XP systems
- Custom commands
- Logging systems
- Auto-moderation
- Ticket systems
- Music/audio features

## Questions?

- **Documentation**: Check the [README](README.md)
- **Issues**: Open a GitHub issue
- **Discussions**: Use GitHub Discussions
- **Website**: Visit https://oak.oakheart.dev

## Code of Conduct

- Be respectful and constructive
- Welcome newcomers
- Focus on the code, not the person
- Give credit where due
- Keep discussions on-topic
- Report inappropriate behavior

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to Oak! 🌳
